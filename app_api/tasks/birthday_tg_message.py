import logging
from celery import shared_task
from app_api.utils.util_birthday import get_children_w_coming_birthday
from app_api.utils.util_name_case import get_first_name, to_genitive
from app_api.tasks.check_clients_balance_and_notify import send_telegram_message_with_result
from app_api.alfa_crm_service.crm_service import find_client_by_id
from app_kiberclub.models import BirthdayMessageStatus


logger = logging.getLogger(__name__)


@shared_task
def send_birthday_tg_message():

    clients = get_children_w_coming_birthday(window_days=7, is_exact_match=True, is_client_obj_need=True)

    sent = 0       # сообщение действительно ушло
    skipped = 0    # напоминание за этот год уже отправляли раньше
    failed = 0     # отправка или обработка завершились ошибкой

    for client in clients:
        client_obj = client['client_obj']

        try:

            crm_data = find_client_by_id(client['branch_id'], client['crm_id'])

            if crm_data is None:
                logger.warning(f"Не удалось получить текущий баланс для клиента {client['name']}")
                balance = "для уточнения баланса обратитесь к менеджеру"
            else:
                balance = f'{crm_data.get("balance")} BYN'

            # Имя ставим в родительный падеж: «у Валерии», а не «у Валерия»
            child_name = to_genitive(get_first_name(client['name']).title())

            text = f"🎈 Через 7 дней у {child_name} День Рождения!\n\n" \
                   f"Вашему ребёнку исполнится {client['upcoming_age']} лет 🎉\n\n" \
                   f"Текущий баланс: {balance}\n\n" \
                   f"Если вы хотите уточнить информацию — свяжитесь с менеджером через меню бота.\n\n" \
                   f"Ваш KLiK! ❤"

            # Отметку создаём до отправки: если она уже была, значит
            # напоминание за этот год ушло раньше и повторять его не нужно
            obj, created = BirthdayMessageStatus.objects.get_or_create(
                client=client_obj,
                year_of_future_birthday_message=client["next_birthday_year"],
            )

            if not created:
                skipped += 1
                continue

            if not send_telegram_message_with_result(client['telegram_id'], text):
                logger.error(
                    f"Не удалось отправить напоминание о дне рождения, crm_id={client['crm_id']}. "
                    f"Отметка удалена, попытка повторится при следующем запуске"
                )
                obj.delete()
                failed += 1
                continue

            sent += 1

        except Exception as exp:
            logger.error(f"Ошибка при обработке напоминания о дне рождения, crm_id={client['crm_id']}: {exp}")
            failed += 1
            continue

    result = (f"Отобрано детей: {len(clients)}. Отправлено: {sent}. "
              f"Пропущено (уже отправляли): {skipped}. Ошибок: {failed}.")
    logger.info(result)
    return result
