import json
from datetime import datetime
import logging
import gspread
import re
import requests
from bs4 import BeautifulSoup
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from oauth2client.service_account import ServiceAccountCredentials

from app_api.alfa_crm_service.crm_service import (
    get_client_lessons,
    get_client_lesson_name,
    get_client_kiberons,
)
from app_kiberclub.models import AppUser, Client, Location, RunningLine
from googleapiclient.discovery import build
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = "kiberone-tg-bot-a43691efe721.json"


def index(request: HttpRequest) -> HttpResponse:
    logger.debug("Начало выполнения функции index")

    context = {
        "title": "KIBERone",
    }

    try:
        telegram_id_from_req = request.GET.get("user_tg_id")
        logger.debug(f"Получен user_tg_id из GET: {telegram_id_from_req}")

        if telegram_id_from_req:
            request.session["tg_id"] = telegram_id_from_req
            logger.debug(f"Сохранён tg_id в сессию: {telegram_id_from_req}")
        else:
            telegram_id_from_req = request.session.get("tg_id")
            if not telegram_id_from_req:
                logger.warning("tg_id отсутствует в сессии и запросе. Перенаправление на страницу ошибки.")
                return redirect("app_kiberclub:error_page")

        bot_user = get_object_or_404(AppUser, telegram_id=telegram_id_from_req)
        logger.debug(f"Найден пользователь: {bot_user.telegram_id}")

        user_clients = Client.objects.filter(user=bot_user)
        logger.debug(f"Найдено профилей клиентов: {user_clients.count()}")
        context.update({"profiles": user_clients})

    except AppUser.DoesNotExist:
        logger.error("Пользователь не найден", exc_info=True)
        return redirect("app_kiberclub:error_page")
    except Exception as e:
        logger.exception(f"Неожиданная ошибка: {e}")
        return redirect("app_kiberclub:error_page")

    logger.debug("Завершение функции index. Рендеринг шаблона.")
    return render(request, "app_kiberclub/index.html", context=context)


def open_profile(request):
    """
    Отображает профиль выбранного клиента.
    """
    logger.debug("Начало выполнения функции open_profile")

    if request.method == "POST":
        client_id = request.POST.get("client_id")
        logger.debug(f"Получен client_id из POST: {client_id}")

        if client_id:
            request.session["client_id"] = client_id
            logger.debug(f"Сохранён client_id в сессию: {client_id}")
        else:
            logger.warning("client_id отсутствует в POST-запросе")
            return redirect("app_kiberclub:error_page")
    else:
        client_id = request.session.get("client_id")
        logger.debug(f"Получен client_id из сессии: {client_id}")

    try:
        client = get_object_or_404(Client, crm_id=client_id)
        logger.debug(f"Найден клиент: {client.crm_id}, имя: {client.name}")

        if not client.is_study:
            logger.warning(f"Клиент {client.crm_id} не имеет статуса обучающегося (is_study=False). Доступ запрещен.")
            return redirect("app_kiberclub:error_page")

        context = {
            "title": "KIBERone - Профиль",
            "client": {
                "client_id": client_id,
                "crm_id": client.crm_id,
                "name": client.name,
                "dob": client.dob.strftime("%d.%m.%Y") if client.dob else "Не указано",
                "balance": client.balance,
                "paid_count": client.paid_lesson_count,
                "next_lesson_date": (client.next_lesson_date.strftime("%d.%m.%Y") if client.next_lesson_date else "Нет запланированных уроков"),
                "paid_till": (client.paid_till.strftime("%d.%m.%Y") if client.paid_till else "Не указано"),
                "note": client.note or "Нет заметок",
                "branch": client.branch.name if client.branch else "Не указано",
                "is_study": "Да" if client.is_study else "Нет",
                "has_scheduled_lessons": ("Да" if client.has_scheduled_lessons else "Нет"),
            },
        }

        portfolio_link = get_portfolio_link(client.name)
        logger.debug(f"Портфолио для клиента {client.name}: {portfolio_link}")
        context.update({"portfolio_link": portfolio_link})

        branch_id = int(client.branch.branch_id) if client.branch else 0
        logger.debug(f"Определён branch_id: {branch_id}")

        # Получаем данные, не зависящие от расписания
        client_resume = get_client_resume(client.crm_id)
        logger.debug(f"Резюме клиента: {client_resume}")

        kiberons = get_client_kiberons(branch_id, client.crm_id) if branch_id else "0"
        logger.debug(f"Кибероны клиента: {kiberons}")

        # Инициализируем переменные для расписания заглушками
        lesson_name = "Занятия отсутствуют"
        location_name = "Локация не назначена"
        room_id = ""

        # Пытаемся получить данные об уроках
        if branch_id:
            lessons_data = get_client_lessons(client_id, branch_id, lesson_status=1, lesson_type=2)
            logger.debug(f"Получены данные об уроках для клиента {client_id}: {lessons_data}")

            if lessons_data and int(lessons_data.get("total", 0)) > 0:
                lesson = lessons_data.get("items", [])[-1]
                lesson_room_id = lesson.get("room_id")
                subject_id = lesson.get("subject_id")
                logger.debug(f"Последний урок: room_id={lesson_room_id}, subject_id={subject_id}")

                lesson_info = get_client_lesson_name(branch_id, subject_id)
                logger.debug(f"Информация о названии урока: {lesson_info}")

                if lesson_info and lesson_info.get("total", 0) > 0:
                    all_lesson_items = lesson_info.get("items", [])
                    for item in all_lesson_items:
                        if item.get("id") == subject_id:
                            lesson_name = item.get("name", "")
                            logger.debug(f"Название урока найдено: {lesson_name}")
                            break

                if lesson_room_id:
                    room_id = lesson_room_id
                    logger.debug(f"Установлен room_id в сессию: {room_id}")
                    request.session["room_id"] = room_id

                    location = Location.objects.filter(location_crm_id=room_id).first()
                    if location:
                        location_name = location.name

        # Формируем итоговый контекст
        context["client"].update(
            {
                "location_name": location_name,
                "lesson_name": lesson_name if lesson_name else "Занятия отсутствуют",
                "resume": (client_resume if client_resume else "Появится позже"),
                "room_id": room_id,
                "kiberons_count": kiberons if kiberons else "0",
            }
        )

        # Add running line to context
        running_line = RunningLine.objects.first()
        if running_line and running_line.is_active:
            context["running_line_text"] = running_line.text
        else:
            context["running_line_text"] = None

        return render(request, "app_kiberclub/client_card.html", context)

    except Exception as e:
        logger.exception(f"Произошла ошибка при выполнении open_profile: {e}")
        return redirect("app_kiberclub:error_page")


def error_page_view(request):
    return render(request, "app_kiberclub/error_page.html")


def get_client_resume(child_id: str) -> str:
    """
    Получение резюме по API
    """
    try:
        url = f"https://kiber-resume.of.by/api/resumes/latest-verified/"
        params = {"student_crm_id": child_id}
        response: HttpResponse = requests.get(url=url, params=params, timeout=5)

        if response.status_code == 200:
            data = response.json()
            resume_content: str = data.get("content", "")
            return resume_content if resume_content else "Появится позже"
        else:
            logger.warning(f"Получен статус {response.status_code} при запросе резюме для клиента {child_id}")
            return "Появится позже"

    except requests.exceptions.RequestException as e:
        logger.exception(f"Ошибка сети при запросе резюме для клиента {child_id}: {e}")
        return "Появится позже"
    except KeyError as e:
        logger.exception(f"Отсутствует ожидаемое поле в ответе API для клиента {child_id}: {e}")
        return "Появится позже"
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при запросе резюме для клиента {child_id}: {e}")
        return "Появится позже"


def save_review_from_page(request):
    """
    Сохранение отзыва по API
    """
    if request.method != "POST":
        logger.warning("Попытка вызвать save_review_from_page с методом, отличным от POST")
        return JsonResponse({"status": "error", "message": "Метод не поддерживается"}, status=405)

    try:
        crm_id = request.POST.get("crm_id")
        feedback = request.POST.get("feedbackInput")

        if not crm_id or not feedback:
            logger.warning(f"Отсутствуют необходимые параметры: crm_id={bool(crm_id)}, feedback={bool(feedback)}")
            return JsonResponse({"status": "error", "message": "Отсутствуют необходимые параметры"}, status=400)

        url = "https://kiber-resume.of.by/api/reviews/"
        data = {"student_crm_id": crm_id, "content": feedback}

        response: HttpResponse = requests.post(url=url, data=data, timeout=5)

        if response.status_code == 200 or response.status_code == 201:
            logger.info(f"Отзыв успешно сохранен для клиента {crm_id}")
            return JsonResponse({"status": "success", "message": "Отзыв успешно сохранен"})
        else:
            logger.warning(f"Получен статус {response.status_code} при сохранении отзыва для клиента {crm_id}")
            return JsonResponse({"status": "error", "message": "Ошибка при сохранении отзыва"}, status=400)

    except requests.exceptions.RequestException as e:
        logger.exception(f"Ошибка сети при отправке отзыва для клиента {crm_id}: {e}")
        return JsonResponse({"status": "error", "message": "Ошибка сети"}, status=500)
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при сохранении отзыва: {e}")
        return JsonResponse({"status": "error", "message": "Внутренняя ошибка сервера"}, status=500)


def get_portfolio_link(client_name) -> str | None:
    SCOPES = ["https://www.googleapis.com/auth/drive "]
    CREDENTIALS_FILE = "portfolio-credentials.json"
    credentials = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)

    drive_service = build("drive", "v3", credentials=credentials)

    client_name = " ".join(client_name.split(" ")[:2])
    query = f"name contains '{client_name}' and mimeType='application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="nextPageToken, files(id, name, mimeType)").execute()

    folders = results.get("files", [])
    if not folders:
        return "#"

    folder_id = folders[0]["id"]
    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    return folder_url
