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
    list_display = ('id', 'status_filter', 'task_id', 'task_status')
    exclude = ('task_id',)
    readonly_fields = ('task_status',)

    def task_status(self, obj):
        if not obj.task_id:
            return "Не запущена"

        task = AsyncResult(obj.task_id)

        if task.state == 'PROGRESS':
            progress = task.info.get('current', 0)
            total = task.info.get('total', 0)
            return f"В процессе ({progress}/{total})"
        elif task.state == 'SUCCESS':
            return f"Завершено (Успешно: {task.result.get('success', 0)}, Ошибки: {task.result.get('fail', 0)})"
        elif task.state == 'FAILURE':
            return "Ошибка при выполнении"
        else:
            return task.state

    task_status.short_description = "Статус задачи"

    def save_model(self, request, obj, form, change):
        obj.sent_by = request.user

        super().save_model(request, obj, form, change)

        # Запускаем задачу Celery
        task = send_broadcast_task.delay(obj.id)
        obj.task_id = task.id
        super().save_model(request, obj, form, change)

        messages.info(request, f"Рассылка запущена как фоновая задача (ID: {task.id})")


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
