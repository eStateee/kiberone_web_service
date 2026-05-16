import datetime
from unittest.mock import patch

from django.test import TestCase, Client as TestClient
from django.urls import reverse

from app_kiberclub.models import Client, AppUser, Branch


@patch('app_kibershop.context_processors.get_client_kiberons', return_value="100")
class OpenProfileTests(TestCase):
    def setUp(self):
        self.client = TestClient()
        self.user = AppUser.objects.create(telegram_id="123456", phone_number="+1234567890", username="Test User")
        self.branch = Branch.objects.create(branch_id="1", name="Test Branch")
        self.client_obj = Client.objects.create(
            user=self.user,
            crm_id="100",
            name="Test Client",
            is_study=True,
            branch=self.branch,
            dob=datetime.date(2010, 1, 1),
            balance=0,
            paid_lesson_count=10,
        )

    @patch('app_kiberclub.views.get_client_lessons')
    @patch('app_kiberclub.views.get_client_resume')
    @patch('app_kiberclub.views.get_client_kiberons')
    @patch('app_kiberclub.views.get_portfolio_link')
    @patch('app_kiberclub.views.get_client_lesson_name')
    def test_open_profile_without_lessons(
        self, mock_get_lesson_name, mock_get_portfolio_link, mock_get_kiberons, mock_get_resume, mock_get_lessons, mock_cp_kiberons
    ):
        """
        Тест проверяет, что если у клиента is_study=True, но нет запланированных уроков,
        его пускает в профиль, и данные отображаются корректно (в виде заглушек для уроков).
        """
        # Настройка моков для возврата "пустых" данных об уроках
        mock_get_lessons.return_value = {"total": 0, "items": []}
        mock_get_resume.return_value = "Test Resume"
        mock_get_kiberons.return_value = "100"
        mock_get_portfolio_link.return_value = "http://portfolio"
        
        # Эмуляция POST-запроса, как это делает фронт-энд (или telegram web app)
        response = self.client.post(reverse('app_kiberclub:open_profile'), {'client_id': '100'})
        
        # Проверяем, что нет редиректа на страницу ошибки
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app_kiberclub/client_card.html')
        
        # Проверяем контекст, переданный в шаблон
        context_client = response.context['client']
        self.assertEqual(context_client['lesson_name'], 'Занятия отсутствуют')
        self.assertEqual(context_client['location_name'], 'Локация не назначена')
        self.assertEqual(context_client['room_id'], '')
        self.assertEqual(context_client['kiberons_count'], '100')
        self.assertEqual(context_client['resume'], 'Test Resume')

    def test_open_profile_is_study_false(self, mock_cp_kiberons):
        """
        Тест проверяет, что если у клиента is_study=False,
        то его не пускает в профиль и редиректит на страницу ошибки.
        """
        # Устанавливаем статус не обучающегося
        self.client_obj.is_study = False
        self.client_obj.save()
        
        response = self.client.post(reverse('app_kiberclub:open_profile'), {'client_id': '100'})
        
        # Проверяем, что произошел редирект на страницу ошибки
        self.assertRedirects(response, reverse('app_kiberclub:error_page'))
