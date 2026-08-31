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

    my_tg_id = "1871915988" # телеграмм id чата для проверки

    clients = get_children_w_coming_birthday(window_days=7, is_exact_match=True, is_client_obj_need=True)

    success = 0
    total = 0
    for client in clients:
        client_obj = client['client_obj']

        try:

            crm_data = find_client_by_id(client['branch_id'], my_tg_id)

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

            obj , created = BirthdayMessageStatus.objects.get_or_create(client=client_obj ,
                                                                        year_of_future_birthday_message=client[
                                                                            "next_birthday_year"])
            if created:
                result = send_telegram_message_with_result(client['telegram_id'], text)
                total += 1
                if not result:
                    logger.error(f"Ошибка при отправки напоминания об дне рождении в телеграмм. "
                             f"Запись не сохранена в БД{client['crm_id']}")
                    obj.delete()
                    continue

                success += 1

        except Exception as exp:
            logger.error(f"Ошибка при отправки напоминания об дне рождении {client['crm_id']}: {exp}")
            continue

    return f"Всего отправлено сообщений {total}. Успешно: {success}."
