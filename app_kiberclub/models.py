from django.db import models


class Branch(models.Model):
    """
    Модель филиала.
    """

    branch_id = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        verbose_name="ID филиала в ЦРМ",
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Название филиала",
    )
    sheet_url = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Ссылка на таблицу",
    )

    def __str__(self):
        return self.name

    class Meta:
        db_table = "branch"
        verbose_name = "Филиал"
        verbose_name_plural = "Филиалы"


class Manager(models.Model):
    name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Имя")
    telegram_link = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Телеграм ссылка",
    )

    def __str__(self):
        return f"Менеджер {self.name}"

    class Meta:
        db_table = "managers"
        verbose_name = "Менеджер"
        verbose_name_plural = "Менеджеры"


class Location(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, verbose_name="Филиал")
    location_crm_id = models.CharField(
        max_length=3,
        unique=True,
        blank=True,
        null=True,
        verbose_name="ID локации в ЦРМ",
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Название Локации",
    )
    sheet_name = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Название листа в таблице",
    )
    map_url = models.CharField(max_length=100, blank=True, null=True, verbose_name="Ссылка на карту")
    location_manager = models.ForeignKey(
        Manager,
        on_delete=models.SET_NULL,
        related_name="managed_locations",
        verbose_name="Менеджер",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.location_crm_id} - {self.name} (Филиал: {self.branch.name})"

    class Meta:
        db_table = "locations"
        verbose_name = "Локация"
        verbose_name_plural = "Локации"


class AppUser(models.Model):
    """
    Модель пользователя (родителя).
    """

    CLIENT_STATUS = (
        ("0", "Lead"),
        ("1", "Lead with group"),
        ("2", "Client"),
    )

    status = models.CharField(choices=CLIENT_STATUS, default="0", max_length=5, verbose_name="Статус клиента")

    telegram_id = models.CharField(max_length=100, unique=True, blank=True, null=True, verbose_name="Телеграм ID")
    username = models.CharField(max_length=100, unique=False, blank=True, null=True, verbose_name="Username")
    phone_number = models.CharField(
        max_length=100,
        unique=False,
        blank=True,
        null=True,
        verbose_name="Номер телефона",
    )

    def __str__(self):
        return f"{self.username or 'Пользователь'} (ID: {self.telegram_id})"

    class Meta:
        db_table = "bot_users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"


class Client(models.Model):
    """
    Модель клиента (ребенка).
    """

    user = models.ForeignKey(
        AppUser,
        on_delete=models.CASCADE,
        related_name="clients",
        verbose_name="Пользователь",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Имя")
    branch = models.ForeignKey("Branch", on_delete=models.CASCADE, verbose_name="Филиал")
    crm_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="ID в CRM")

    is_study = models.BooleanField(default=False, verbose_name="Является клиентом")
    dob = models.DateField(blank=True, null=True, verbose_name="Дата рождения")
    balance = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Баланс")
    next_lesson_date = models.DateTimeField(blank=True, null=True, verbose_name="Дата следующего занятия")
    paid_till = models.DateField(blank=True, null=True, verbose_name="Оплачено до")
    note = models.TextField(blank=True, null=True, verbose_name="Примечание")
    paid_lesson_count = models.IntegerField(blank=True, null=True, verbose_name="Количество оплаченных занятий")
    has_scheduled_lessons = models.BooleanField(
        default=False,
        verbose_name="Есть запланированные уроки",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.name or 'noname'} | (Родитель: {self.user})"

    class Meta:
        db_table = "clients"
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"


class SalesManager(models.Model):
    name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Имя")
    telegram_link = models.CharField(max_length=100, unique=True, blank=True, null=True, verbose_name="Телеграм ссылка")

    class Meta:
        db_table = "sales_managers"
        verbose_name = "Менеджер по продажам"
        verbose_name_plural = "Менеджеры по продажам"

    def __str__(self):
        return f"Менеджер по продажам {self.name}"


class QuestionsAnswers(models.Model):
    question = models.CharField(max_length=255, blank=True, null=True, verbose_name="Вопрос")
    answer = models.CharField(blank=True, null=True, verbose_name="Ответ")

    class Meta:
        db_table = "questions_answers"
        verbose_name = "Вопрос-Ответ"
        verbose_name_plural = "Вопросы-Ответы"

    def __str__(self):
        return f"{self.question}"


class EripPaymentHelp(models.Model):
    erip_link = models.CharField(max_length=355, blank=True, null=True, verbose_name="Ссылка")
    erip_instructions = models.CharField(max_length=1355, blank=True, null=True, verbose_name="Инструкция")

    class Meta:
        db_table = "erip_payment_help"
        verbose_name = "Помощь в оплате ЕРИП"
        verbose_name_plural = "Помощь в оплате ЕРИП"


class PartnerCity(models.Model):
    name = models.CharField(max_length=255, verbose_name="Название")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        db_table = "partner_city"
        verbose_name = "Город партнера"
        verbose_name_plural = "Города партнеров"

    def __str__(self):
        return self.name


class PartnerCategory(models.Model):
    name = models.CharField(max_length=255, verbose_name="Название")

    class Meta:
        db_table = "partner_bonus_category"
        verbose_name = "Категория партнерского бонуса"
        verbose_name_plural = "Категории партнерских бонусов клиента"

    def __str__(self):
        return f"{self.name}"


class PartnerClientBonus(models.Model):
    category = models.ForeignKey(PartnerCategory, on_delete=models.CASCADE, verbose_name="Категория")
    cities = models.ManyToManyField(PartnerCity, verbose_name="Города", related_name="partners")
    partner_name = models.CharField(max_length=155, verbose_name="Название партнера")
    description = models.TextField(verbose_name="Описание", null=True, blank=True)
    terms_of_usage = models.TextField(verbose_name="Условия использования", null=True, blank=True)
    code = models.CharField(max_length=155, verbose_name="Промо-код", null=True, blank=True)
    image = models.ImageField(upload_to="partner_bonuses/", blank=True, null=True, verbose_name="Изображение")

    class Meta:
        db_table = "partner_bonus"
        verbose_name = "Партнер и его бонус (промокод)"
        verbose_name_plural = "Партнеры и их бонусы (промокоды)"

    def __str__(self):
        return f"{self.partner_name} - {self.category}"


class ClientBonus(models.Model):
    bonus = models.CharField(max_length=255, verbose_name="Бонус")
    description = models.CharField(max_length=255, verbose_name="Описание")

    class Meta:
        db_table = "client_bonus"
        verbose_name = "Бонус для клиента"
        verbose_name_plural = "Бонусы для клиента"


class SocialLink(models.Model):
    name = models.CharField(max_length=255, verbose_name="Название")
    link = models.CharField(max_length=255, verbose_name="Ссылка")

    class Meta:
        db_table = "social_links"
        verbose_name = "Социальная ссылка"
        verbose_name_plural = "Социальные ссылки"


class BroadcastMessage(models.Model):
    message_text = models.TextField(verbose_name="Текст сообщения")
    image = models.ImageField(upload_to="broadcast_images/", blank=True, null=True, verbose_name="Изображение")
    status_filter = models.CharField(max_length=5, choices=AppUser.CLIENT_STATUS, blank=True, null=True, verbose_name="Фильтр по статусу (оставьте пустым для всех)")
    task_id = models.CharField(max_length=255, blank=True, null=True, verbose_name="ID задачи Celery")

    class Meta:
        verbose_name = "Рассылка"
        verbose_name_plural = "Рассылки"


class GiftLink(models.Model):
    """
    Модель для хранения ссылки на подарок
    """

    title = models.CharField(max_length=255, verbose_name="Название", default="Ссылка для подарка")
    url = models.URLField(verbose_name="Ссылка для подарка")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Ссылка для подарка"
        verbose_name_plural = "Ссылки для подарка"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class RunningLine(models.Model):
    """
    Модель для бегущей строки на странице клиента
    """

    text = models.TextField(verbose_name="Текст бегущей строки", blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Показывать бегущую строку")

    class Meta:
        verbose_name = "Бегущая строка"
        verbose_name_plural = "Бегущие строки"

    def __str__(self):
        return f"Бегущая строка: {'Активна' if self.is_active else 'Неактивна'}"
