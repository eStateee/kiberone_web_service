import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import sleep
import requests
import redis
from dotenv import load_dotenv

from app_kiberclub.models import Branch
from celery_app import app

load_dotenv()

logger = logging.getLogger(__name__)

CRM_HOSTNAME = os.getenv("CRM_HOSTNAME")
CRM_EMAIL = os.getenv("CRM_EMAIL")
CRM_API_KEY = os.getenv("CRM_API_KEY")

BASE_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "application/json, text/plain, */*",
}

branches = [1, 2, 3, 4]
client_is_study_statuses = [0, 1]

REQUEST_LIMIT = 2  # Максимальное количество одновременных запросов
MAX_RETRIES = 5  # Максимальное количество попыток
RETRY_DELAY = 2  # Начальная задержка между попытками


def get_redis_client():
    """
    Создает и возвращает клиент Redis.
    """
    return redis.StrictRedis(host="localhost", port=6379, db=0, decode_responses=True)


@app.task
def update_crm_token():
    """
    Задача для обновления токена в Redis.
    """
    token = login_to_alfa_crm()
    if token:
        # Сохраняем токен в Redis с TTL 55 минут (3300 секунд)
        redis_client = get_redis_client()
        redis_client.set("crm_token", token, ex=3300)
        logger.info("Токен успешно обновлен и сохранен в Redis.")
    else:
        logger.error("Не удалось обновить токен.")


def get_crm_token():
    """
    Получение токена из Redis или через авторизацию.
    """
    redis_client = get_redis_client()
    token = redis_client.get("crm_token")

    if token:
        logger.info("Токен успешно получен из Redis.")
        return token
    else:
        logger.info("Токен отсутствует в Redis. Запрашиваем новый токен...")
        token = login_to_alfa_crm()
        if token:
            # Сохраняем токен в Redis с TTL 55 минут (3300 секунд)
            redis_client.set("crm_token", token, ex=3300)
            logger.info("Новый токен сохранен в Redis.")
            return token
        else:
            logger.error("Не удалось получить токен.")
            return None


def login_to_alfa_crm():
    """
    Авторизация в CRM и получение токена.
    """
    email = os.getenv("CRM_EMAIL")
    api_key = os.getenv("CRM_API_KEY")
    logger.info(f"Начинается авторизация в CRM с email: {email}...")

    data = {"email": email, "api_key": api_key}
    url = f"https://{CRM_HOSTNAME}/v2api/auth/login"
    logger.debug(f"URL для авторизации: {url}")
    logger.debug(f"Данные для авторизации: {data}")

    try:
        logger.info("Отправка POST-запроса для авторизации...")
        # Без таймаута запрос висит бесконечно, если CRM не отвечает,
        # и вместе с ним зависает вызвавшая его страница или задача
        response = requests.post(url, headers=BASE_HEADERS, json=data, timeout=10)
        logger.debug(
            f"Получен ответ от сервера: статус {response.status_code}, тело: {response.text}"
        )

        if response.status_code == 200:
            token_data = response.json()
            token = token_data.get("token")
            logger.info(f"Токен успешно получен: {token[:10]}... (первые 10 символов)")
            return token
        else:
            logger.error(f"Ошибка авторизации: статус {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Произошла ошибка при отправке запроса для авторизации: {e}")
        return None


def find_user_by_phone(phone_number: str) -> dict | None:
    """
    Поиск пользователя по номеру телефона.
    """
    logger.info(f"Начинается поиск пользователя по номеру телефона: {phone_number}")

    def fetch_data(branch: str, status: int) -> dict | None:
        """
        Выполнение одного запроса к CRM.
        """
        data = {"is_study": status, "page": 0, "phone": phone_number}
        url = f"https://{CRM_HOSTNAME}/v2api/{branch}/customer/index"
        logger.info(
            f"Выполняется запрос для branch={branch}, status={status}, URL: {url}"
        )
        logger.debug(f"Данные для запроса: {data}")

        response = send_request_to_crm(url=url, data=data, params=None)
        if response:
            logger.info(f"Успешный ответ для branch={branch}, status={status}")
        else:
            logger.warning(
                f"Не удалось получить данные для branch={branch}, status={status}"
            )
        return response

    tasks = [
        (str(branch), status)
        for status in client_is_study_statuses
        for branch in branches
    ]
    logger.info(
        f"Сформированы задачи для выполнения: {len(tasks)} комбинаций филиалов и статусов."
    )

    results = []
    with ThreadPoolExecutor(max_workers=REQUEST_LIMIT) as executor:
        logger.info(
            f"Запуск выполнения задач с ограничением {REQUEST_LIMIT} одновременных запросов."
        )
        futures = [
            executor.submit(fetch_data, branch, status) for branch, status in tasks
        ]
        for future in futures:
            result = future.result()
            if result is not None:
                results.append(result)
            else:
                logger.warning("Получен пустой результат, пропускаем.")

    # Обработка результатов
    total_sum = sum(int(result.get("total", 0)) for result in results)
    count_sum = sum(int(result.get("count", 0)) for result in results)
    all_items = [item for result in results for item in result.get("items", [])]

    result_answer = {
        "total": total_sum,
        "count": count_sum,
        "items": all_items,
    }
    return result_answer


def create_user_in_crm(user_data) -> dict | None:
    """
    Создание нового пользователя в CRM.
    """
    logger.info("Начинается процесс создания нового пользователя в CRM.")

    client_name = (
        f"{user_data['first_name']} {user_data['last_name']} | {user_data['username']}"
    )
    data = {
        "name": client_name,
        "phone": user_data["phone_number"],
        "branch_ids": 1,
        "legal_type": 1,
        "is_study": 0,
        "note": "created by Telegram BOT",
    }
    url = f"https://{CRM_HOSTNAME}/v2api/1/customer/create"

    logger.info(f"Отправка данных для создания пользователя: {data}")
    try:
        response: dict = send_request_to_crm(url=url, data=data, params=None)
        if response:
            logger.info("Пользователь успешно создан.")
            return response
        else:
            logger.error(f"Ошибка создания пользователя, тело: {response}")
            return None
    except Exception as e:
        logger.error(f"Произошла ошибка при создании пользователя: {e}")
        return None


def send_request_to_crm(url: str, data: dict, params: dict | None) -> dict | None:
    token = get_crm_token()
    if not token:
        logger.error("Токен отсутствует. Отмена запроса.")
        return None

    headers = {**BASE_HEADERS, "X-ALFACRM-TOKEN": token}
    retry_delay = RETRY_DELAY
    logger.info(
        f"Начинается отправка запроса к CRM. URL: {url}, Данные: {data}, Параметры: {params}"
    )

    for attempt in range(MAX_RETRIES):
        try:
            logger.info(
                f"Попытка {attempt + 1}/{MAX_RETRIES}. Отправка POST-запроса..."
            )
            with ThreadPoolExecutor(max_workers=REQUEST_LIMIT) as executor:
                future = executor.submit(
                    requests.post,
                    url,
                    headers=headers,
                    json=data,
                    params=params,
                    timeout=10,
                )
                response = future.result()

            logger.debug(
                f"Получен ответ от сервера: статус {response.status_code}, тело: {response.text}"
            )

            if response.status_code == 200:
                logger.info("Запрос успешно выполнен.")
                try:
                    return response.json()
                except json.JSONDecodeError:
                    logger.error("Ошибка декодирования JSON. Ответ: %s", response.text)
                    return None
            elif response.status_code == 401:
                logger.error("Неавторизованный запрос. Отмена запроса.")
                return None
            elif response.status_code == 429:
                logger.warning(
                    f"Слишком много запросов. Повторная попытка через {retry_delay} секунд..."
                )
                sleep(retry_delay)
                retry_delay *= 2
                continue
            else:
                logger.error(
                    f"Неожиданный статус: {response.status_code}. Тело: {response.text}"
                )
                return None
        except requests.RequestException as e:
            logger.error(f"Ошибка при отправке запроса: {e}")
            return None

    logger.error("Достигнуто максимальное количество попыток. Запрос не выполнен.")
    return None


def get_client_lessons(
    user_crm_id: int,
    branch_id: int,
    page: int | None = None,
    lesson_status: int = 1,
    lesson_type: int = 2,
) -> dict | None:
    data = {
        "customer_id": user_crm_id,
        "status": lesson_status,  # 1 - запланирован урок, 2 - отменен, 3 - проведен
        "lesson_type_id": lesson_type,  # 3 - пробный, 2 - групповой
        "page": 0 if page is None else page,
    }

    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/lesson/index"

    response_data: dict | None = send_request_to_crm(url, data, params=None)
    if response_data:
        if isinstance(response_data, dict) and "total" in response_data:
            logger.info(f"Получено уроков: {response_data.get('total')}")
            return response_data
        else:
            logger.error(f"Некорректный ответ от CRM: {response_data}")
            return {"total": 0}
    else:
        logger.warning(f"Не удалось получить данные уроков")
        return {"total": 0}


def get_taught_trial_lesson(customer_id, branch_id):
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/lesson/index"

    data = {
        "customer_id": customer_id,
        "status": 3,  # 1 - запланирован урок, 2 - отменен, 3 - проведен
        "lesson_type_id": 3  # 3 - пробник, 2 - групповой
    }

    # lessons = requests.post(url, json=data, headers=headers)
    lessons_response = send_request_to_crm(url=url, data=data, params=None)

    return lessons_response


def get_curr_tariff(user_crm_id, branch_id, curr_date):
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/customer-tariff/index?customer_id={user_crm_id}"
    customer_tariffs = send_request_to_crm(url, {}, None)
    for tariff in sorted(customer_tariffs.get("items"), key=lambda x: datetime.strptime(x.get("e_date"), "%d.%m.%Y")):
        tariff_end_date = datetime.strptime(tariff.get("e_date"), "%d.%m.%Y")
        tariff_begin_date = datetime.strptime(tariff.get("b_date"), "%d.%m.%Y")
        if tariff_end_date.date() >= curr_date >= tariff_begin_date.date():
            price = float(get_tariff_price(branch_id, tariff.get("tariff_id")))
            discount = float(get_curr_discount(branch_id, user_crm_id, curr_date))
            tariff.update({"price": price * (1 - discount / 100)})
            return tariff


def get_tariff_price(branch_id, tariff_id):
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/tariff/index"
    page = 0
    data = {"page": 0}
    tariff_objects = send_request_to_crm(url, data, None)
    tariff_objects_items = tariff_objects.get("items")
    last_page = 1
    if tariff_objects.get("count") is not None and int(tariff_objects.get("count")) != 0:
        last_page = int(tariff_objects.get("total", 0)) // int(tariff_objects.get("count", 1))
    while page <= last_page:
        for tariff in tariff_objects_items:
            if tariff.get("id") == tariff_id:
                return tariff.get("price")
        page += 1
        data = {"page": page}
        tariff_objects_items = send_request_to_crm(url, data, None).get("items")
    return 0


def get_curr_discount(branch_id, user_crm_id, curr_date):
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/discount/index"
    page = 0
    data = {"customer_id": user_crm_id, "page": 0}
    discounts = send_request_to_crm(url, data, None)
    discounts_items = discounts.get("items")
    last_page = 1
    if discounts.get("count") is not None and int(discounts.get("count")) != 0:
        last_page = int(discounts.get("total", 0)) // int(discounts.get("count", 1))
    while page < last_page:
        for discount in sorted(
            discounts_items, key=lambda x: datetime.strptime(x.get("end"), "%d.%m.%Y")
        ):
            discount_end_date = datetime.strptime(discount.get("end"), "%d.%m.%Y")
            discount_begin_date = datetime.strptime(discount.get("begin"), "%d.%m.%Y")
            if discount_end_date.date() >= curr_date >= discount_begin_date.date():
                return discount.get("amount")
        page += 1
        data.update({"page": page})
        discounts = send_request_to_crm(url, data, None)
        discounts_items = discounts.get("items")
    return 0


def get_client_lesson_name(branch_id: int, subject_id: int | None = None) -> dict | None:
    data = {"id": subject_id, "active": True, "page": 0}
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/subject/index"
    response_data = send_request_to_crm(
        url,
        data,
        params=None,
    )
    if response_data and response_data.get("total") != 0:
        return response_data
    return {"total": 0}


def get_user_groups_from_crm(branch_id: int, user_crm_id: int) -> dict | None:
    data = {"page": 0}
    params = {
        "customer_id": user_crm_id,
    }
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/cgi/customer"

    logger.debug("Попытка получить группы пользователя (ID)")
    response_data = send_request_to_crm(url=url, data=data, params=params)
    if response_data and response_data.get("total", 0) != 0:
        return response_data
    return {"total": 0}


def get_group_link_from_crm(branch_id: int, group_id: int) -> dict | None:
    data = {"id": group_id, "page": 0}
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/group/index"

    response_data = send_request_to_crm(url=url, data=data, params=None)
    if response_data:
        if response_data.get("total") != 0:
            return response_data
        return {"total": 0}
    else:
        logger.debug("Не удалось получить ссылку на группу.")
        return {"total": 0}


def find_client_by_id(branch_id, crm_id) -> dict | None:
    # Добавляем обязательные параметры фильтрации
    data = {
        "id": crm_id,
        "is_study": 2,  # 1 - клиенты, 0 - лиды, 2 - все
        "page": 0
    }
    
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/customer/index"
    
    try:
        response = send_request_to_crm(url=url, data=data, params=None)
        
        if not response:
            logger.error("Пустой ответ от CRM")
            return None
            
        # Проверяем наличие items в ответе
        clients = response.get("items", [])
        
        if not clients:
            logger.error(f"Клиент с ID {crm_id} не найден")
            return None
            
        if len(clients) > 1:
            logger.warning(f"Найдено несколько клиентов с ID {crm_id}, возвращаем первого")
            
        logger.info(f"Клиент {crm_id} успешно найден")
        return clients[0]
        
    except Exception as e:
        logger.error(f"Ошибка при поиске клиента: {e}")
        return None


def get_manager_from_crm(branch_id, page=0):
    data = {"page": page}
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/user/index"
    try:
        response: dict = send_request_to_crm(url=url, data=data, params=None)
        if response:
            logger.info("Менеджеры получены")
            return response
        else:
            logger.error(f"Менеджер не найден: {response}")
            return None
    except Exception as e:
        return None


def set_client_kiberons(branch_id, customer_id, kiberons_from_kiberclub):
    try:
        url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/bonus/bonus-add?customer_id={customer_id}"
        data = {
            "amount": kiberons_from_kiberclub 
        }
        response: dict = send_request_to_crm(url=url, data=data, params=None)

        if response:
            logger.info("Запрос для установки числа киберонов успешный")
            return response
        else:
            logger.error(f"Запрос для установки числа киберонов не успешный: {response}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при установке Киберонов: {e}")
        return None


def spent_client_kiberons(branch_id, customer_id, count, note=""):
    try:
        url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/bonus/bonus-spend?customer_id={customer_id}"
        data = {
            "amount": count,
            "note": note,
            }
        response: dict = send_request_to_crm(url=url, data=data, params=None)

        if response:
            logger.info("Запрос для списания Киберонов успешный")
            return response
        else:
            logger.error(f"Запрос для списания Киберонов не успешный: {response}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при списании Киберонов: {e}")
        return None


def get_client_kiberons(branch_id, customer_id):
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/bonus/balance-bonus?customer_id={customer_id}"
    
    response: dict = send_request_to_crm(url=url, data=None, params=None)
    if response:
        logger.info("Запрос для получения числа киберонов успешный")
        if 'balance_bonus' in response:
            return response['balance_bonus']
        return 0
    else:
        logger.error(f"Запрос для получения числа киберонов не успешный: {response}")
        return None


def get_all_clients(branch_id):
    url = f"https://{CRM_HOSTNAME}/v2api/{branch_id}/customer/index"
    
    page = 0
    count = 1
    
    while count > 0:
        data = {
            "is_study": 0,
            "page": page
        }
        clients_response = send_request_to_crm(url=url, data=data, params=None)
        
        if not clients_response:
            logger.error(f"Не удалось получить клиентов для филиала {branch_id}, страница {page}")
            break
            
        if "items" in clients_response:
            clients = clients_response["items"]
            count = clients_response.get("count", 0)
            logger.info(f"Получено {len(clients)} клиентов для филиала {branch_id}, страница {page}")
            
            # Возвращаем клиентов по одному через yield
            for client in clients:
                yield client
                
        else:
            logger.error(f"Неверный формат ответа от CRM для филиала {branch_id}, страница {page}")
            break
            
        page += 1
    
    logger.info(f"Завершено получение клиентов для филиала {branch_id}")


def get_teacher(branch, phone_number):
    url = f"https://kiberoneminsk.s20.online/v2api/{branch}/teacher/index"
    data = {"phone": phone_number}
    response = send_request_to_crm(url=url, data=data, params=None)
    return response


def get_teacher_group(branch, teacher_id):
    url = f"https://kiberoneminsk.s20.online/v2api/{branch}/group/index"

    data = {
        "teacher_id": teacher_id
    }

    groups_res = send_request_to_crm(url=url, data=data, params=None)
    all_groups = groups_res.get('items', [])
    if all_groups:
        teacher_id_int = int(teacher_id)
        filtered_groups = []
        for group in all_groups:
            if any(teacher['id'] == teacher_id_int for teacher in group.get('teachers', [])):
                filtered_groups.append(group)
        return filtered_groups
    return None


def get_clients_in_group(group_id, branch):
    url = f"https://kiberoneminsk.s20.online/v2api/{branch}/cgi/index?group_id={group_id}"

    cgi_res = send_request_to_crm(url=url, data=None, params=None)
    customer_ids = [customer_id['customer_id'] for customer_id in cgi_res.get('items', [])]
    client_names = []
    
    for customer_id in customer_ids:
        client_data = find_client_by_id(branch, int(customer_id))
        if client_data:
            client_name = client_data.get('name', 'Неизвестный клиент')
            client_names.append(client_name)
        else:
            client_names.append('Клиент не найден')
    
    clients_in_group = [{'customer_id': customer_id, 'client_name': client_name} 
                       for customer_id, client_name in zip(customer_ids, client_names)]
    return clients_in_group


def get_all_groups():
    try:
        branches_ = Branch.objects.all()
    except Branch.DoesNotExist:
        return []

    all_items = []
    for branch in branches_:
        url = f"https://kiberoneminsk.s20.online/v2api/{branch.branch_id}/group/index"
        page = 0

        while True:
            response: dict = send_request_to_crm(url=url, data={"page": page}, params=None)
            items = response.get("items", [])
            current_page_count = len(items)  # или response.get("count", 0), если API возвращает это поле
            total = response.get("total", 0)

            if current_page_count == 0:
                break  # больше нет данных

            all_items.extend(items)
            page += 1

            # Дополнительная защита: если уже собрали все записи
            if len(all_items) >= total > 0:
                break

    return all_items
