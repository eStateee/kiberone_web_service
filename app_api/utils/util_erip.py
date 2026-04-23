import hashlib
import hmac
import logging
import os
from datetime import datetime

import requests
from dateutil.relativedelta import relativedelta

from app_api.alfa_crm_service.crm_service import get_client_lessons, get_curr_tariff

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

DEFAULT_PAY_URL = os.getenv("DEFAULT_PAY_URL")
EXPRESS_PAY_URL = os.getenv("EXPRESS_PAY_URL")
EXPRESS_PAY_TOKEN = os.getenv("EXPRESS_PAY_TOKEN")


def set_pay(user_data):
    balance: float = float(user_data.get("balance"))
    amount_payable = get_paid_summ(user_data, balance, datetime.now().date())
    clear_user_not_paid_invoices('1-' + user_data.get("crm_id"))
    pay_url = (get_pay_url(user_data.get("crm_id"), round(amount_payable + 0.001, 2), user_data.get("name")))
    message = (f"ФИО: {user_data.get('name').title()}\n"
               f"Сумма к оплате: {round(amount_payable + 0.001, 2)}\n"
               f"Ссылка для оплаты: {pay_url}")
    return message


def get_signature(data):
    key = "Kiber".encode("utf-8")
    raw = data.encode("utf-8")

    digester = hmac.new(key, raw, hashlib.sha1)
    signature = digester.hexdigest()

    return signature.upper()


def get_paid_summ(user_data, user_balance, curr_date):

    lesson_price = round(get_lesson_price(user_data.get("crm_id"), user_data.get("branch_id"), curr_date) + 0.001, 2)

    taught_dates_dict, plan_dates_dict = get_curr_month_lessons(user_data, curr_date)

    if len(taught_dates_dict) + len(plan_dates_dict) == 0:
        if user_balance < 0:
            return abs(user_balance)
        else:
            taught_dates_dict, plan_dates_dict = get_curr_month_lessons(user_data, curr_date + relativedelta(months=1))
            if len(plan_dates_dict) == 0:
                return 0
            else:
                return get_paid_summ(user_data, user_balance, curr_date + relativedelta(months=1))

    amount_payable = user_balance - lesson_price * len(plan_dates_dict)

    if amount_payable < 0:
        return abs(amount_payable)
    else:
        return get_paid_summ(user_data, amount_payable, curr_date + relativedelta(months=1))


def get_curr_month_lessons(user_data, curr_date):

    taught_lesson_dates = []
    plan_lesson_dates = []

    taught_lessons = get_client_lessons(user_data.get("crm_id"), user_data.get("branch_id", 0), None, 3)
    taught_lessons = taught_lessons.get("items", [])

    plan_lessons = get_client_lessons(user_data.get("crm_id"), user_data.get("branch_id", 0), None, 1)
    plan_lessons = plan_lessons.get("items", [])

    for lesson in taught_lessons:
        #reason_id = lesson.get("details")[0].get("reason_id")
        logger.info(f'LESSONS GET: {lesson.get("details")}')
        print('LESSON GET: ',lesson.get("details"))
        reason_id = [details.get("reason_id") for details in lesson.get("details")
                     if details["customer_id"] == user_data["crm_id"]][0]
        lesson_date = datetime.strptime(lesson.get("date"), '%Y-%m-%d')
        if lesson_date.month == curr_date.month and lesson_date.year == curr_date.year and reason_id != 1:
            taught_lesson_dates.append({"date": lesson.get("date"), "reason": reason_id})

    for lesson in plan_lessons:
        reason_id = [details.get("reason_id") for details in lesson.get("details")
                     if details["customer_id"] == user_data["crm_id"]][0]
        lesson_date = datetime.strptime(lesson.get("date"), '%Y-%m-%d')
        if lesson_date.month == curr_date.month and lesson_date.year == curr_date.year and reason_id != 1:
            plan_lesson_dates.append({"date": lesson_date, "reason": reason_id})

    return taught_lesson_dates, plan_lesson_dates


def get_lesson_price(user_crm_id, branch_id, curr_date):
    tariff = get_curr_tariff(user_crm_id, branch_id, curr_date)
    price = tariff.get("price") / 4
    return price


def get_pay_url(crm_id, amount, name):
    url = EXPRESS_PAY_URL + "invoices?token=" + EXPRESS_PAY_TOKEN
    params = {
        "Token": EXPRESS_PAY_TOKEN,
        "AccountNo": "1-" + str(crm_id),
        "Amount": str(amount),
        "Currency": "933",
        "Surname": str(name),
        "FirstName": "",
        "Patronymic": "",
        "IsNameEditable": "1",
        "IsAmountEditable": "0",
        "ReturnInvoiceUrl": "1",
    }

    data = ""
    for p in params.values():
        data += p

    params["signature"] = get_signature(data)

    res = requests.post(url, data=params).json()

    return res.get("InvoiceUrl", DEFAULT_PAY_URL)


def get_invoices(crm_id):
    print(f"[DEBUG] Получение списка счетов для пользователя: {crm_id}")

    url = EXPRESS_PAY_URL + "invoices"

    params = {
        "Token": EXPRESS_PAY_TOKEN,
        "AccountNo": crm_id,
        "Status": 1
    }

    data = ""
    for p in params.values():
        data += str(p)

    signature = get_signature(data)
    params["signature"] = signature

    add_url = "?token=" + EXPRESS_PAY_TOKEN
    add_url += "&AccountNo=" + str(crm_id)
    add_url += "&Status=1&signature=" + signature

    response = requests.get(url + add_url, data=params).json()
    print(f"[DEBUG] Получено счетов: {len(response.get('Items', []))}")

    return response


def clear_user_not_paid_invoices(crm_id):

    url = EXPRESS_PAY_URL + "invoices"
    res = get_invoices(crm_id)

    invoices = res.get("Items", [])

    for inv in invoices:
        invoice_no = inv.get("InvoiceNo")

        params = {
            "Token": EXPRESS_PAY_TOKEN,
            "InvoiceNo": invoice_no
        }

        data = ""
        for p in params.values():
            data += str(p)

        signature = get_signature(data)
        params["signature"] = signature

        add_url = '/' + str(inv.get("InvoiceNo"))
        add_url += "?token=" + EXPRESS_PAY_TOKEN
        add_url += "&InvoiceNo=" + str(inv.get("InvoiceNo"))
        add_url += "&signature=" + signature

        response = requests.delete(url + add_url, data=params)
