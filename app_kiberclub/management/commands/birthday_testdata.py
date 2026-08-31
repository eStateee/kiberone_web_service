"""
Подготовка тестовых данных для проверки напоминаний о дне рождения.

Команда временно меняет несколько записей в базе, чтобы у них оказался
день рождения через нужное число дней, и запоминает исходное состояние
в файле. Второй режим возвращает всё обратно.

Использование:

    manage.py birthday_testdata prepare --telegram-id 123456789
    manage.py birthday_testdata restore

Файл со снимком состояния лежит рядом с manage.py и попадает под
правило *.json в .gitignore, поэтому в репозиторий не уедет.

ВНИМАНИЕ: команда меняет боевой слепок базы. Пока снимок не восстановлен,
у выбранных детей стоит неверная дата рождения и чужой родитель.
Всегда запускайте restore после проверки.
"""

import json
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app_kiberclub.models import AppUser, BirthdayMessageStatus, Client

BACKUP_FILE = settings.BASE_DIR / "birthday_testdata_backup.json"


class Command(BaseCommand):
    help = "Готовит тестовых детей с ближайшим днём рождения и возвращает данные обратно"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["prepare", "restore"], help="что сделать")
        parser.add_argument("--telegram-id", help="ваш Telegram ID (обязателен для prepare)")
        parser.add_argument("--count", type=int, default=3, help="сколько детей подготовить, по умолчанию 3")
        parser.add_argument("--days", type=int, default=7, help="через сколько дней день рождения, по умолчанию 7")

    def handle(self, *args, **options):
        if options["action"] == "prepare":
            self.prepare(options)
        else:
            self.restore()

    # ------------------------------------------------------------------ prepare

    @transaction.atomic
    def prepare(self, options):
        if BACKUP_FILE.exists():
            raise CommandError(
                f"Снимок {BACKUP_FILE.name} уже существует. Сначала выполните restore, "
                f"иначе исходные данные будут потеряны."
            )

        telegram_id = options["telegram_id"]
        if not telegram_id:
            raise CommandError("Укажите --telegram-id, иначе сообщение будет некому отправить")

        count = options["count"]
        target = date.today() + timedelta(days=options["days"])

        # Берём детей, у которых заполнено всё нужное для запроса баланса в CRM.
        clients = list(
            Client.objects.select_related("branch")
            .exclude(dob__isnull=True)
            .exclude(crm_id__isnull=True)
            .exclude(crm_id="")
            .filter(branch__isnull=False)[:count]
        )
        if len(clients) < count:
            raise CommandError(f"В базе нашлось только {len(clients)} подходящих детей из {count}")

        user, user_created = AppUser.objects.get_or_create(
            telegram_id=str(telegram_id),
            defaults={"username": "birthday_testdata", "status": "2"},
        )

        snapshot = {
            "telegram_id": str(telegram_id),
            "user_id": user.pk,
            "user_created": user_created,
            "target_date": target.isoformat(),
            "clients": [],
            # Запоминаем уже существующие отметки, чтобы при restore удалить
            # только те, что создаст задача во время проверки.
            "existing_statuses": list(BirthdayMessageStatus.objects.values_list("pk", flat=True)),
        }

        self.stdout.write(self.style.MIGRATE_HEADING("Подготовлены дети:"))

        for client in clients:
            snapshot["clients"].append(
                {"pk": client.pk, "dob": client.dob.isoformat(), "user_id": client.user_id}
            )

            client.dob = self._shift_birthday(client.dob, target)
            client.user = user
            client.save(update_fields=["dob", "user"])

            self.stdout.write(
                f"  {client.name} | crm_id={client.crm_id} | филиал={client.branch.name} | "
                f"новая дата рождения {client.dob.strftime('%d.%m.%Y')}"
            )

        BACKUP_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write("")
        self.stdout.write(f"Родитель: telegram_id={telegram_id} ({'создан' if user_created else 'уже был в базе'})")
        self.stdout.write(f"День рождения у всех: {target.strftime('%d.%m.%Y')}, это через {options['days']} дн.")
        self.stdout.write(f"Снимок сохранён: {BACKUP_FILE.name}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Не забудьте нажать Start у тестового бота — иначе Telegram не пропустит сообщение."))
        self.stdout.write(self.style.WARNING("После проверки обязательно: manage.py birthday_testdata restore"))

    @staticmethod
    def _shift_birthday(original: date, target: date) -> date:
        """
        Переносит день и месяц на нужную дату, оставляя год рождения —
        чтобы возраст в сообщении остался правдоподобным.
        """
        try:
            return original.replace(month=target.month, day=target.day)
        except ValueError:
            # 29 февраля в невисокосном году рождения — сдвигаем на день назад
            return original.replace(month=target.month, day=target.day - 1)

    # ------------------------------------------------------------------ restore

    @transaction.atomic
    def restore(self):
        if not BACKUP_FILE.exists():
            raise CommandError(f"Файл {BACKUP_FILE.name} не найден — восстанавливать нечего")

        snapshot = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))

        # Сначала отметки: они ссылаются на детей.
        new_statuses = BirthdayMessageStatus.objects.exclude(pk__in=snapshot["existing_statuses"])
        removed_statuses = new_statuses.count()
        new_statuses.delete()

        # Потом дети — их надо вернуть прежним родителям до удаления тестового,
        # иначе каскад унесёт их вместе с ним.
        restored = 0
        for item in snapshot["clients"]:
            client = Client.objects.filter(pk=item["pk"]).first()
            if not client:
                self.stdout.write(self.style.WARNING(f"  ребёнок id={item['pk']} не найден, пропускаю"))
                continue
            client.dob = date.fromisoformat(item["dob"])
            client.user_id = item["user_id"]
            client.save(update_fields=["dob", "user"])
            restored += 1

        # И только теперь тестовый родитель, если его создавали мы.
        user_removed = False
        if snapshot["user_created"]:
            user_removed = AppUser.objects.filter(pk=snapshot["user_id"]).delete()[0] > 0

        BACKUP_FILE.unlink()

        self.stdout.write(self.style.SUCCESS("Данные восстановлены"))
        self.stdout.write(f"  детей возвращено в исходное состояние: {restored}")
        self.stdout.write(f"  удалено отметок об отправке: {removed_statuses}")
        self.stdout.write(f"  тестовый родитель удалён: {'да' if user_removed else 'нет, он был в базе до проверки'}")
