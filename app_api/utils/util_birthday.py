from app_kiberclub.models import Client, BirthdayMessageStatus
from django.utils import timezone


def get_children_w_coming_birthday(window_days=7, telegram_id=None, is_exact_match: bool = False, is_client_obj_need=False):
    start_d = timezone.localdate()
    current_year = start_d.year

    clients = Client.objects.exclude(dob__isnull=True).select_related("user").select_related("branch")

    if telegram_id:
        clients = clients.filter(user__telegram_id=telegram_id)

    data = []

    for client in clients:

        try:
            exp_coming_birthday = client.dob.replace(year=current_year)
            coming_birthday = exp_coming_birthday.replace(year=current_year+1) if exp_coming_birthday < start_d else exp_coming_birthday
        except ValueError:
            # пропускаем ребенка с днем рождения в високосного год, если сейчас год не является таким
            continue

        days_until = (coming_birthday - start_d).days

        if days_until <= window_days:

            if is_exact_match and days_until != window_days:
                # в случае celery нам нужно получить дни рождение точно через n дней, поэтому отсеиваем другие варианты
                continue

            client_data = {
                    "client_id": client.pk,
                    "name": client.name,
                    "dob": client.dob,
                    "upcoming_age": coming_birthday.year - client.dob.year,
                    "days_until": days_until,
                    "crm_id": client.crm_id,
                    "telegram_id": client.user.telegram_id,
                    "next_birthday_year": coming_birthday.year,
                }

            if is_client_obj_need:
                client_data["client_obj"] = client

            data.append(client_data)

    return data