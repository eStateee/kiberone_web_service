import logging
from time import sleep, monotonic

import requests
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

from .models import BroadcastMessage, AppUser

logger = logging.getLogger(__name__)

# HTTP-коды Telegram, при которых telegram_id невалиден
TELEGRAM_INVALID_CHAT_CODES = {400, 403}

# Подстроки ошибок Telegram, указывающие на невалидного пользователя
TELEGRAM_INVALID_DESCRIPTIONS = [
    "chat not found",
    "bot was blocked by the user",
    "user is deactivated",
    "bot can't initiate conversation",
    "peer_id_invalid",
    "chat_write_forbidden",
    "user_is_blocked",
    "bot was kicked",
]

# Лимит Telegram на длину подписи к фото
TELEGRAM_CAPTION_LIMIT = 1024

# Как часто сохранять прогресс рассылки в БД (каждые N получателей)
PROGRESS_SAVE_EVERY = 20

# Пауза между сообщениями, чтобы не упираться в лимиты Telegram (~30 msg/sec)
SEND_DELAY = 0.05

# Сколько раз пробуем загрузить изображение, прежде чем остановить рассылку
MAX_UPLOAD_ATTEMPTS = 20

# Свои лимиты времени для рассылки: глобальный CELERY_TASK_TIME_LIMIT (30 мин)
# для неё слишком мал. Мягкий лимит даёт возможность сохранить прогресс до kill.
BROADCAST_SOFT_TIME_LIMIT = 6 * 60 * 60
BROADCAST_TIME_LIMIT = BROADCAST_SOFT_TIME_LIMIT + 5 * 60


@shared_task(
    bind=True,
    soft_time_limit=BROADCAST_SOFT_TIME_LIMIT,
    time_limit=BROADCAST_TIME_LIMIT,
)
def send_broadcast_task(self, broadcast_id):
    """
    Задача Celery для отправки рассылки.

    Особенности:
      * изображение загружается в Telegram один раз, дальше переиспользуется file_id
        (иначе каждый получатель = повторная загрузка файла, и рассылка не успевает
        уложиться в лимит времени задачи);
      * прогресс пишется в БД, поэтому задачу можно продолжить с места остановки —
        повторный запуск не отправит сообщение тем, кто его уже получил;
      * невалидные telegram_id очищаются, чтобы не тратить на них время впредь.

    По умолчанию рассылка идёт только клиентам (status="2").
    """
    broadcast = BroadcastMessage.objects.get(id=broadcast_id)

    # Фильтруем пользователей с непустым telegram_id
    users = AppUser.objects.exclude(telegram_id__isnull=True).exclude(telegram_id__exact='')

    if broadcast.status_filter:
        # Если в рассылке указан фильтр — используем его
        users = users.filter(status=broadcast.status_filter)
    else:
        # По умолчанию рассылка только клиентам (is_study=True → status "2")
        users = users.filter(status="2")

    # Список id материализуем заранее: внутри цикла мы меняем telegram_id
    # (очистка невалидных), и итерироваться по этому же queryset нельзя.
    all_ids = list(users.order_by("id").values_list("id", flat=True))

    processed_ids = set(broadcast.processed_ids or [])
    pending_ids = [user_id for user_id in all_ids if user_id not in processed_ids]

    total = len(all_ids)
    success = broadcast.sent_count
    fail = broadcast.failed_count
    skipped = len(processed_ids)

    broadcast.status = BroadcastMessage.STATUS_RUNNING
    broadcast.total = total
    broadcast.task_id = self.request.id or broadcast.task_id
    broadcast.started_at = broadcast.started_at or timezone.now()
    broadcast.finished_at = None
    broadcast.last_error = ""
    broadcast.save(update_fields=[
        "status", "total", "task_id", "started_at", "finished_at", "last_error",
    ])

    logger.info(
        f"Начало рассылки #{broadcast_id}. Всего получателей: {total}, "
        f"к отправке: {len(pending_ids)}, обработано в предыдущих запусках: {skipped}"
    )

    started = monotonic()
    i = 0
    upload_attempts = 0

    try:
        for i, user_id in enumerate(pending_ids, 1):
            user = AppUser.objects.filter(id=user_id).first()
            if user is None or not user.telegram_id:
                # Пользователь удалён синхронизацией с CRM или telegram_id уже очищен
                processed_ids.add(user_id)
                continue

            try:
                result = _send_broadcast_to_user(broadcast, user.telegram_id)
            except SoftTimeLimitExceeded:
                # Лимит времени обрабатываем снаружи, глотать его нельзя
                raise
            except Exception as e:
                logger.error(f"Ошибка при отправке пользователю {user.telegram_id}: {e}")
                result = "error"

            if result == "success":
                success += 1
                processed_ids.add(user_id)
            elif result == "invalid_chat":
                # Невалидный чат — очищаем telegram_id, чтобы не тратить на него время
                logger.warning(
                    f"Пользователь {user.id} (tg_id={user.telegram_id}) — невалидный чат. "
                    f"Очищаю telegram_id."
                )
                user.telegram_id = None
                user.save(update_fields=["telegram_id"])
                fail += 1
                processed_ids.add(user_id)
            else:
                # Временная ошибка — не помечаем обработанным, повторим при возобновлении
                fail += 1

            if broadcast.image and not broadcast.photo_file_id and result == "error":
                # Пока не получен file_id, каждый получатель = повторная загрузка файла.
                # Считаем только настоящие сбои загрузки — невалидные чаты (result ==
                # "invalid_chat") не говорят о проблеме с самим изображением: если среди
                # первых получателей окажется много мёртвых telegram_id, останавливать
                # рассылку из-за этого не нужно.
                upload_attempts += 1
                if upload_attempts >= MAX_UPLOAD_ATTEMPTS:
                    broadcast.status = BroadcastMessage.STATUS_INTERRUPTED
                    broadcast.last_error = (
                        f"Не удалось загрузить изображение в Telegram за "
                        f"{upload_attempts} попыток — рассылка остановлена. "
                        f"Проверьте файл изображения и логи воркера."
                    )
                    broadcast.finished_at = timezone.now()
                    _save_progress(broadcast, success, fail, processed_ids, extra_fields=[
                        "status", "last_error", "finished_at",
                    ])
                    logger.error(
                        f"Рассылка #{broadcast_id} остановлена: изображение не загружается в Telegram."
                    )
                    return {
                        'total': total,
                        'success': success,
                        'fail': fail,
                        'skipped': skipped,
                        'aborted': True,
                        'broadcast_id': broadcast_id,
                    }

            if i % PROGRESS_SAVE_EVERY == 0:
                _save_progress(broadcast, success, fail, processed_ids)
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'current': skipped + i,
                        'total': total,
                        'success': success,
                        'fail': fail,
                        'skipped': skipped,
                    }
                )

            if SEND_DELAY:
                sleep(SEND_DELAY)

    except SoftTimeLimitExceeded:
        # Не даём воркеру убить задачу молча: фиксируем, докуда дошли
        broadcast.status = BroadcastMessage.STATUS_INTERRUPTED
        broadcast.last_error = (
            f"Превышен лимит времени задачи. Обработано {skipped + i} из {total}. "
            f"Запустите рассылку повторно — она продолжится с этого места."
        )
        broadcast.finished_at = timezone.now()
        _save_progress(broadcast, success, fail, processed_ids, extra_fields=[
            "status", "last_error", "finished_at",
        ])
        logger.error(
            f"Рассылка #{broadcast_id} прервана по лимиту времени на {skipped + i}/{total}."
        )
        return {
            'total': total,
            'success': success,
            'fail': fail,
            'skipped': skipped,
            'interrupted': True,
            'broadcast_id': broadcast_id,
        }

    remaining = len([user_id for user_id in all_ids if user_id not in processed_ids])
    broadcast.status = (
        BroadcastMessage.STATUS_DONE if remaining == 0 else BroadcastMessage.STATUS_INTERRUPTED
    )
    broadcast.finished_at = timezone.now()
    if remaining:
        broadcast.last_error = (
            f"{remaining} получателей не обработано из-за временных ошибок Telegram. "
            f"Можно запустить рассылку повторно."
        )
    _save_progress(broadcast, success, fail, processed_ids, extra_fields=[
        "status", "finished_at", "last_error",
    ])

    logger.info(
        f"Рассылка #{broadcast_id} завершена за {int(monotonic() - started)} с. "
        f"Всего: {total}, успешно: {success}, ошибок: {fail}, "
        f"пропущено (обработаны ранее): {skipped}"
    )

    return {
        'total': total,
        'success': success,
        'fail': fail,
        'skipped': skipped,
        'broadcast_id': broadcast_id,
    }


def _save_progress(broadcast, success, fail, processed_ids, extra_fields=None):
    """
    Сохраняет прогресс рассылки в БД.
    """
    broadcast.sent_count = success
    broadcast.failed_count = fail
    broadcast.processed_ids = sorted(processed_ids)
    fields = ["sent_count", "failed_count", "processed_ids"] + (extra_fields or [])
    broadcast.save(update_fields=fields)


def _send_broadcast_to_user(broadcast, chat_id):
    """
    Отправляет сообщение рассылки одному получателю.
    Изображение уходит по file_id, полученному при первой загрузке.
    """
    if not broadcast.image:
        return send_telegram_message(chat_id=chat_id, text=broadcast.message_text)

    if not broadcast.photo_file_id:
        # Первая отправка: загружаем файл и запоминаем file_id
        result, file_id = _send_photo_upload(broadcast, chat_id)
        if file_id:
            broadcast.photo_file_id = file_id
            broadcast.save(update_fields=["photo_file_id"])
            logger.info(
                f"Рассылка #{broadcast.id}: изображение загружено в Telegram, "
                f"file_id получен — дальше переиспользуем его."
            )
        return result

    return send_telegram_message(
        chat_id=chat_id,
        text=broadcast.message_text,
        photo_file_id=broadcast.photo_file_id,
    )


def _send_photo_upload(broadcast, chat_id):
    """
    Загружает изображение рассылки в Telegram (multipart) и возвращает
    (результат, file_id самой большой версии фото).
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен в settings.py")
        return "error", None

    if broadcast.image.size > 10 * 1024 * 1024:
        logger.error(f"Изображение слишком большое: {broadcast.image.name}")
        return "error", None

    caption, extra_text = _split_caption(broadcast.message_text)

    try:
        broadcast.image.open('rb')
        try:
            response = requests.post(
                _api_url("sendPhoto"),
                files={'photo': broadcast.image.file},
                data={'chat_id': chat_id, 'caption': caption},
                timeout=(10, 120),
            )
        finally:
            broadcast.image.close()
    except requests.RequestException as e:
        logger.error(f"Ошибка загрузки изображения рассылки в чат {chat_id}: {e}")
        return "error", None

    result = _classify_response(response, chat_id)
    if result != "success":
        return result, None

    file_id = _extract_file_id(response)

    if extra_text:
        send_telegram_message(chat_id=chat_id, text=extra_text)

    return "success", file_id


def send_telegram_message(chat_id, text, photo_file_id=None):
    """
    Функция отправки сообщения через Telegram API.
    Возвращает:
        "success" — сообщение отправлено
        "invalid_chat" — чат невалиден (chat not found, blocked, deactivated)
        "error" — другая ошибка
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен в settings.py")
        return "error"

    extra_text = None

    if photo_file_id:
        url = _api_url("sendPhoto")
        caption, extra_text = _split_caption(text)
        data = {'chat_id': chat_id, 'photo': photo_file_id, 'caption': caption}
    else:
        url = _api_url("sendMessage")
        data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}

    for attempt in range(2):
        try:
            response = requests.post(url, data=data, timeout=(10, 30))
        except requests.Timeout:
            logger.error(f"Таймаут при отправке сообщения в чат {chat_id}")
            return "error"
        except requests.RequestException as e:
            logger.error(f"Ошибка при отправке сообщения в чат {chat_id}: {e}")
            return "error"

        result = _classify_response(response, chat_id)

        if result == "rate_limited":
            if attempt == 0:
                sleep(_retry_after(response))
                continue
            return "error"

        if result == "success" and extra_text:
            send_telegram_message(chat_id=chat_id, text=extra_text)

        return result

    return "error"


def _api_url(method):
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


def _split_caption(text):
    """
    Telegram ограничивает подпись к фото 1024 символами.
    Длинный текст режем: часть уходит подписью, остаток — отдельным сообщением.
    """
    text = text or ""
    if len(text) <= TELEGRAM_CAPTION_LIMIT:
        return text, None
    return text[:TELEGRAM_CAPTION_LIMIT], text[TELEGRAM_CAPTION_LIMIT:]


def _retry_after(response):
    try:
        return min(int(response.json().get("parameters", {}).get("retry_after", 5)), 60)
    except (ValueError, AttributeError, TypeError):
        return 5


def _extract_file_id(response):
    try:
        photos = response.json().get("result", {}).get("photo") or []
        return photos[-1]["file_id"] if photos else None
    except (ValueError, KeyError, TypeError, IndexError):
        logger.warning("Не удалось получить file_id загруженного изображения.")
        return None


def _classify_response(response, chat_id):
    """
    Классифицирует ответ Telegram API.
    """
    if response.status_code == 200:
        return "success"

    if response.status_code == 429:
        logger.warning(f"Telegram API: лимит запросов (429) на чате {chat_id} — {response.text}")
        return "rate_limited"

    if response.status_code in TELEGRAM_INVALID_CHAT_CODES:
        response_text = response.text.lower()
        for description in TELEGRAM_INVALID_DESCRIPTIONS:
            if description in response_text:
                logger.warning(
                    f"Telegram API: невалидный чат {chat_id} — {response.text}"
                )
                return "invalid_chat"

    logger.error(f"Ошибка Telegram API ({chat_id}): {response.status_code} - {response.text}")
    return "error"
