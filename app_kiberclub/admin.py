import logging
from time import sleep
from .models import GiftLink
import requests
from django.conf import settings
from django.contrib import admin, messages
from .models import BroadcastMessage, AppUser
from .tasks import send_broadcast_task
from celery.result import AsyncResult

from app_kiberclub.models import (
    AppUser,
    Client,
    Branch,
    QuestionsAnswers,
    EripPaymentHelp,
    PartnerCategory,
    PartnerClientBonus,
    ClientBonus,
    SalesManager,
    SocialLink,
    Location,
    Manager, BroadcastMessage, RunningLine, PartnerCity,
    SummerGlobalConfig, SummerCity, SummerFormat,
)

logger = logging.getLogger(__name__)


class ClientInline(admin.TabularInline):
    """
    Inline для редактирования клиентов на странице пользователя.
    """

    model = Client
    extra = 1  # Количество пустых форм для добавления новых клиентов
    fields = [
        "branch",
        "name",
        "crm_id",
        "is_study",
        "has_scheduled_lessons",
    ]  # Поля для отображения
    readonly_fields = ["crm_id"]  # Если crm_id не должен редактироваться


@admin.register(AppUser)
class BotUserAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели BotUser.
    """

    list_display = ["phone_number", "telegram_id", "username", "client_count"]
    search_fields = ["telegram_id", "phone_number"]
    inlines = [ClientInline]  # Добавляем inline для клиентов

    def client_count(self, obj):
        """
        Отображает количество клиентов у пользователя.
        """
        return obj.clients.count()

    client_count.short_description = "Количество детей"


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели Client.
    """

    list_display = ["__str__", "branch", "crm_id", "is_study"]
    list_filter = ["is_study", "branch"]
    search_fields = ["crm_id", "user__username", "user__telegram_id"]


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели Branch.
    """

    list_display = ["name"]
    search_fields = ["name"]


@admin.register(QuestionsAnswers)
class QuestionsAnswersAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели QuestionsAnswers.
    """

    list_display = ["question", "answer"]
    search_fields = ["question", "answer"]

    class Meta:
        verbose_name = "Вопрос-Ответ"
        verbose_name_plural = "Вопросы-Ответы"


@admin.register(EripPaymentHelp)
class EripPaymentHelpAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели EripPaymentHelp.
    """

    list_display = ["erip_link", "erip_instructions"]
    search_fields = ["erip_link", "erip_instructions"]


@admin.register(PartnerCity)
class PartnerCityAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    list_editable = ["is_active"]
    search_fields = ["name"]


@admin.register(PartnerCategory)
class PartnerCategoryAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели PartnerCategory.
    """

    list_display = ["name"]

@admin.register(PartnerClientBonus)
class PartnerClientBonusAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели PartnerClientBonus.
    """

    list_display = ["partner_name", "category"]
    filter_horizontal = ('cities',)
    search_fields = ["partner_name"]


@admin.register(ClientBonus)
class ClientBonusAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели ClientBonus.
    """

    list_display = ["bonus"]


@admin.register(SalesManager)
class SalesManagerAdmin(admin.ModelAdmin):
    list_display = ("name", "telegram_link")


@admin.register(SocialLink)
class SocialLinkAdmin(admin.ModelAdmin):
    list_display = ("name", "link")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("branch", "name", "location_crm_id")
    list_filter = ["branch"]


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ("name", "telegram_link")


@admin.register(BroadcastMessage)
class BroadcastMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'status_filter', 'status', 'progress', 'task_status')
    list_filter = ('status',)
    exclude = ('task_id', 'processed_ids', 'photo_file_id', 'total', 'sent_count', 'failed_count')
    readonly_fields = (
        'status', 'progress', 'task_status', 'started_at', 'finished_at', 'last_error',
    )
    actions = ('start_or_resume_broadcast',)

    def progress(self, obj):
        return (
            f"{len(obj.processed_ids or [])} из {obj.total} "
            f"(успешно: {obj.sent_count}, ошибок: {obj.failed_count})"
        )

    progress.short_description = "Прогресс"

    def task_status(self, obj):
        """
        Состояние celery-задачи. Основной источник правды — поля модели,
        AsyncResult нужен только чтобы показать сбой самой задачи.
        """
        if not obj.task_id:
            return "Не запущена"

        task = AsyncResult(obj.task_id)

        if task.state == 'PROGRESS' and isinstance(task.info, dict):
            return f"В процессе ({task.info.get('current', 0)}/{task.info.get('total', 0)})"
        if task.state == 'FAILURE':
            return "Задача упала с ошибкой (см. «Последняя ошибка» и логи воркера)"
        return task.state

    task_status.short_description = "Статус задачи"

    def save_model(self, request, obj, form, change):
        message_changed = (
            change and form.changed_data
            and bool({'message_text', 'image'} & set(form.changed_data))
        )

        if message_changed:
            # Текст/картинку поменяли — это новое сообщение, прогресс прошлой
            # отправки больше не действителен.
            obj.processed_ids = []
            obj.photo_file_id = ""
            obj.sent_count = 0
            obj.failed_count = 0
            obj.started_at = None
            obj.finished_at = None
            obj.last_error = ""
            obj.status = BroadcastMessage.STATUS_PENDING

        super().save_model(request, obj, form, change)

        if change:
            # Редактирование существующей рассылки не должно рассылать повторно.
            messages.info(
                request,
                "Изменения сохранены. Рассылка не запущена: выберите её в списке и "
                "примените действие «Запустить / продолжить рассылку»."
            )
            return

        self._launch(request, obj)

    def start_or_resume_broadcast(self, request, queryset):
        for broadcast in queryset:
            if broadcast.status == BroadcastMessage.STATUS_RUNNING:
                messages.warning(
                    request, f"Рассылка #{broadcast.id} уже отправляется — пропускаю."
                )
                continue
            self._launch(request, broadcast)

    start_or_resume_broadcast.short_description = "Запустить / продолжить рассылку"

    @staticmethod
    def _launch(request, broadcast):
        task = send_broadcast_task.delay(broadcast.id)
        BroadcastMessage.objects.filter(pk=broadcast.pk).update(task_id=task.id)

        already_done = len(broadcast.processed_ids or [])
        suffix = f" Пропустим {already_done} уже обработанных получателей." if already_done else ""
        messages.info(
            request,
            f"Рассылка #{broadcast.id} запущена как фоновая задача (ID: {task.id}).{suffix}"
        )


admin.site.register(GiftLink)


@admin.register(RunningLine)
class RunningLineAdmin(admin.ModelAdmin):
    list_display = ('id', 'text', 'is_active')
    list_editable = ('is_active',)
    list_filter = ('is_active',)
    search_fields = ('text',)
    list_per_page = 20

    def has_add_permission(self, request):
        # Allow only one instance of RunningLine
        count = RunningLine.objects.count()
        if count == 0:
            return True
        return False


# ==================== ЛЕТО С KLIK ====================


class SummerFormatInline(admin.TabularInline):
    """Inline-форматы для редактирования на странице города."""
    model = SummerFormat
    extra = 1
    fields = ["button_name", "text", "image", "order", "clicks"]
    readonly_fields = ["clicks"]


@admin.register(SummerGlobalConfig)
class SummerGlobalConfigAdmin(admin.ModelAdmin):
    list_display = ["__str__", "is_active", "main_button_clicks", "away_camp_clicks"]
    readonly_fields = ["main_button_clicks", "away_camp_clicks"]

    def has_add_permission(self, request):
        # Singleton: разрешаем создать только одну запись
        return SummerGlobalConfig.objects.count() == 0

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SummerCity)
class SummerCityAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "order", "clicks"]
    list_editable = ["is_active", "order"]
    readonly_fields = ["clicks"]
    inlines = [SummerFormatInline]


@admin.register(SummerFormat)
class SummerFormatAdmin(admin.ModelAdmin):
    list_display = ["button_name", "city", "order", "clicks"]
    list_filter = ["city"]
    list_editable = ["order"]
    readonly_fields = ["clicks"]
