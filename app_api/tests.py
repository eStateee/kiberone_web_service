"""
Тесты фичи «Напоминание о дне рождения ребёнка».

Обращений к AlfaCRM и к Telegram здесь нет: оба внешних сервиса
подменяются заглушками, поэтому тесты проходят без VPN и без сети.

Запуск:
    manage.py test app_api
"""

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from app_api.tasks.birthday_tg_message import send_birthday_tg_message
from app_api.utils.util_birthday import get_children_w_coming_birthday
from app_api.utils.util_name_case import get_first_name, to_genitive
from app_kiberclub.models import AppUser, BirthdayMessageStatus, Branch, Client

# Фиксированная «сегодняшняя» дата, чтобы тесты не зависели от календаря
TODAY = date(2026, 9, 1)


def fixed_today():
    """Подменяет timezone.localdate внутри модуля выборки."""
    return patch("app_api.utils.util_birthday.timezone.localdate", return_value=TODAY)


class NameCaseTests(TestCase):
    """Склонение имени в родительный падеж для текста сообщения."""

    def test_женские_имена(self):
        self.assertEqual(to_genitive("Валерия"), "Валерии")
        self.assertEqual(to_genitive("Анна"), "Анны")
        self.assertEqual(to_genitive("Ольга"), "Ольги")  # после «г» пишется «и»
        self.assertEqual(to_genitive("Любовь"), "Любови")

    def test_мужские_имена(self):
        self.assertEqual(to_genitive("Иван"), "Ивана")
        self.assertEqual(to_genitive("Андрей"), "Андрея")
        self.assertEqual(to_genitive("Дмитрий"), "Дмитрия")
        self.assertEqual(to_genitive("Игорь"), "Игоря")

    def test_беглая_гласная(self):
        self.assertEqual(to_genitive("Пётр"), "Петра")
        self.assertEqual(to_genitive("Павел"), "Павла")
        self.assertEqual(to_genitive("Лев"), "Льва")

    def test_несклоняемые_остаются_как_есть(self):
        self.assertEqual(to_genitive("Отто"), "Отто")
        self.assertEqual(to_genitive("Мари"), "Мари")
        self.assertEqual(to_genitive("test"), "test")  # латиница не склоняется

    def test_имя_достаётся_из_фио(self):
        # В CRM данные лежат как «Фамилия Имя Отчество»
        self.assertEqual(get_first_name("Чепик Валерия Витальевна"), "Валерия")
        self.assertEqual(get_first_name("Пашков Климентий Семёнович"), "Климентий")

    def test_неполное_фио_не_ломает_разбор(self):
        self.assertEqual(get_first_name("test"), "test")
        self.assertEqual(get_first_name(""), "")


class BirthdaySelectionTests(TestCase):
    """Выборка детей с приближающимся днём рождения."""

    def setUp(self):
        self.branch = Branch.objects.create(branch_id="1", name="Минск")
        self.user = AppUser.objects.create(telegram_id="111", username="parent", status="2")

    def _child(self, name, dob, crm_id="100", user=None):
        return Client.objects.create(
            name=name, dob=dob, branch=self.branch, crm_id=crm_id, user=user or self.user
        )

    def test_находит_ребёнка_через_семь_дней(self):
        self._child("Иванов Иван Иванович", date(2015, 9, 8))
        with fixed_today():
            result = get_children_w_coming_birthday(window_days=7, is_exact_match=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["days_until"], 7)
        self.assertEqual(result[0]["upcoming_age"], 11)
        self.assertEqual(result[0]["next_birthday_year"], 2026)

    def test_точное_совпадение_отсеивает_остальные_дни(self):
        self._child("Иванов Иван Иванович", date(2015, 9, 8), crm_id="1")
        self._child("Петров Пётр Петрович", date(2015, 9, 5), crm_id="2")
        with fixed_today():
            exact = get_children_w_coming_birthday(window_days=7, is_exact_match=True)
            window = get_children_w_coming_birthday(window_days=7)
        self.assertEqual(len(exact), 1)
        self.assertEqual(len(window), 2)

    def test_день_рождения_прошёл_переносится_на_следующий_год(self):
        self._child("Сидоров Сидор Сидорович", date(2015, 8, 20))
        with fixed_today():
            result = get_children_w_coming_birthday(window_days=400)
        self.assertEqual(result[0]["next_birthday_year"], 2027)
        self.assertEqual(result[0]["upcoming_age"], 12)

    def test_переход_через_новый_год(self):
        """Родившийся 3 января: 28 декабря напоминание относится к следующему году."""
        self._child("Зимний Январь Январьевич", date(2015, 1, 3))
        with patch("app_api.utils.util_birthday.timezone.localdate", return_value=date(2026, 12, 28)):
            result = get_children_w_coming_birthday(window_days=7)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["days_until"], 6)
        # Год отметки — год праздника (2027), а не год отправки (2026)
        self.assertEqual(result[0]["next_birthday_year"], 2027)

    def test_ребёнок_без_даты_рождения_пропускается(self):
        self._child("Безымянный Никто Никтович", None)
        with fixed_today():
            self.assertEqual(get_children_w_coming_birthday(window_days=400), [])

    def test_двадцать_девятое_февраля_пропускается(self):
        # 2027 не високосный, такой даты в нём нет
        self._child("Високосный Февраль Февралевич", date(2016, 2, 29))
        with patch("app_api.utils.util_birthday.timezone.localdate", return_value=date(2026, 3, 1)):
            self.assertEqual(get_children_w_coming_birthday(window_days=400), [])

    def test_фильтр_по_родителю(self):
        other = AppUser.objects.create(telegram_id="222", username="other", status="2")
        self._child("Мой Ребёнок Иванович", date(2015, 9, 8), crm_id="1")
        self._child("Чужой Ребёнок Петрович", date(2015, 9, 8), crm_id="2", user=other)
        with fixed_today():
            result = get_children_w_coming_birthday(window_days=7, telegram_id="111")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Мой Ребёнок Иванович")


class UpcomingBirthdaysApiTests(TestCase):
    """Эндпоинт GET /api/upcoming_birthdays/"""

    def setUp(self):
        self.url = reverse("app_crm_api:upcoming_birthdays")
        self.branch = Branch.objects.create(branch_id="1", name="Минск")
        self.user = AppUser.objects.create(telegram_id="111", username="parent", status="2")
        Client.objects.create(
            name="Чепик Валерия Витальевна", dob=date(2014, 9, 8),
            branch=self.branch, crm_id="7121", user=self.user,
        )

    def test_без_telegram_id_возвращает_400(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_у_родителя_без_детей_пустой_список(self):
        AppUser.objects.create(telegram_id="333", username="empty", status="2")
        with fixed_today():
            response = self.client.get(self.url, {"telegram_id": "333"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "data": []})

    @patch("app_api.views.find_client_by_id", return_value={"balance": "150.00"})
    def test_возвращает_ребёнка_с_балансом(self, _crm):
        with fixed_today():
            response = self.client.get(self.url, {"telegram_id": "111", "window_days": "30"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(
            set(data[0]),
            {"client_id", "name", "dob", "upcoming_age", "days_until", "balance"},
        )
        self.assertEqual(data[0]["dob"], "2014-09-08")
        self.assertEqual(data[0]["days_until"], 7)
        self.assertEqual(data[0]["upcoming_age"], 12)
        self.assertEqual(data[0]["balance"], "150.00")

    @patch("app_api.views.find_client_by_id", return_value=None)
    def test_недоступная_crm_не_роняет_ответ(self, _crm):
        with fixed_today():
            response = self.client.get(self.url, {"telegram_id": "111"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["data"][0]["balance"])

    @patch("app_api.views.find_client_by_id", return_value={"balance": "1"})
    def test_служебные_поля_наружу_не_отдаются(self, _crm):
        with fixed_today():
            response = self.client.get(self.url, {"telegram_id": "111"})
        for hidden in ("crm_id", "branch_id", "telegram_id", "next_birthday_year"):
            self.assertNotIn(hidden, response.json()["data"][0])


class BirthdayReminderTaskTests(TestCase):
    """Celery-задача: отправка напоминания и защита от повторов."""

    def setUp(self):
        self.branch = Branch.objects.create(branch_id="1", name="Минск")
        self.user = AppUser.objects.create(telegram_id="111", username="parent", status="2")
        self.child = Client.objects.create(
            name="Чепик Валерия Витальевна", dob=date(2014, 9, 8),
            branch=self.branch, crm_id="7121", user=self.user,
        )

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=True)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value={"balance": "150.00"})
    def test_отправляет_и_создаёт_отметку(self, _crm, send):
        with fixed_today():
            send_birthday_tg_message()
        send.assert_called_once()
        chat_id, text = send.call_args[0]
        self.assertEqual(chat_id, "111")
        self.assertIn("Валерии", text)          # имя в родительном падеже
        self.assertIn("12 лет", text)
        self.assertIn("150.00 BYN", text)
        self.assertEqual(BirthdayMessageStatus.objects.count(), 1)
        self.assertEqual(
            BirthdayMessageStatus.objects.first().year_of_future_birthday_message, 2026
        )

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=True)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value={"balance": "150.00"})
    def test_повторный_запуск_ничего_не_отправляет(self, _crm, send):
        with fixed_today():
            send_birthday_tg_message()
            send_birthday_tg_message()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(BirthdayMessageStatus.objects.count(), 1)

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=False)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value={"balance": "150.00"})
    def test_неудачная_отправка_откатывает_отметку(self, _crm, _send):
        with fixed_today():
            send_birthday_tg_message()
        # Отметки нет — значит завтра задача попробует снова
        self.assertEqual(BirthdayMessageStatus.objects.count(), 0)

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=True)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value=None)
    def test_недоступная_crm_не_отменяет_поздравление(self, _crm, send):
        with fixed_today():
            send_birthday_tg_message()
        send.assert_called_once()
        self.assertIn("обратитесь к менеджеру", send.call_args[0][1])

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=True)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value={"balance": "150.00"})
    def test_счётчики_различают_отправку_и_пропуск(self, _crm, _send):
        with fixed_today():
            first = send_birthday_tg_message()
            second = send_birthday_tg_message()
        self.assertIn("Отправлено: 1", first)
        self.assertIn("Пропущено (уже отправляли): 0", first)
        self.assertIn("Отправлено: 0", second)
        self.assertIn("Пропущено (уже отправляли): 1", second)

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=False)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value={"balance": "1"})
    def test_неудачная_отправка_попадает_в_счётчик_ошибок(self, _crm, _send):
        with fixed_today():
            result = send_birthday_tg_message()
        self.assertIn("Отправлено: 0", result)
        self.assertIn("Ошибок: 1", result)

    @patch("app_api.tasks.birthday_tg_message.send_telegram_message_with_result", return_value=True)
    @patch("app_api.tasks.birthday_tg_message.find_client_by_id", return_value={"balance": "1"})
    def test_ребёнок_вне_окна_не_попадает_в_рассылку(self, _crm, send):
        self.child.dob = date(2014, 10, 20)
        self.child.save()
        with fixed_today():
            send_birthday_tg_message()
        send.assert_not_called()
        self.assertEqual(BirthdayMessageStatus.objects.count(), 0)
