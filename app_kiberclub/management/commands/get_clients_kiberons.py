import csv
import os
from django.core.management.base import BaseCommand
from app_kiberclub.models import Client
from app_kiberclub.views import get_kiberons_count
from app_api.alfa_crm_service.crm_service import set_client_kiberons, get_client_kiberons, get_all_clients
import json
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Получает баланс кликоинов всех клиентов и сохраняет в CSV-файл'

    def handle(self, *args, **options):
        # Создаем директорию fixtures, если она не существует
        fixtures_dir = 'fixtures'
        os.makedirs(fixtures_dir, exist_ok=True)
        
        # Путь к файлу результатов
        output_file = os.path.join(fixtures_dir, 'clients_kiberons.csv')
        
        # Загружаем учетные данные для доступа к киберонам
        try:
            with open("kiberclub_credentials.json", "r", encoding="utf-8") as f:
                credentials = json.load(f)
                self.stdout.write(self.style.SUCCESS('Учетные данные загружены успешно'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка при загрузке учетных данных: {e}'))
            return
        
        # Получаем всех клиентов из базы данных
        branch_ids = [1, 2, 3, 4]
        for branch in branch_ids:
            clients = get_all_clients(branch)
            
            # Проверяем, существует ли файл, и если нет, создаем его с заголовками
            file_exists = os.path.isfile(output_file)
            if not file_exists:
                with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
                    fieldnames = ['name', 'branch_id', 'balance', 'crm_update_status']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                
            # Обрабатываем каждого клиента
            for client in clients:
                try:
                    # Получаем имя и филиал клиента
                    name = client.get('name', 'Неизвестно')
                    branch_id = int(client.get('branch_ids', 1)[0])
                    crm_id = client.get('id', 0)
                    
                    # Определяем логин и пароль для филиала
                    login, password = None, None
                    
                    if branch_id == 1 and 'Минск' in credentials:
                        login = credentials['Минск']['логин']
                        password = credentials['Минск']['пароль']
                    elif branch_id == 2 and 'Барановичи' in credentials:
                        login = credentials['Барановичи']['логин']
                        password = credentials['Барановичи']['пароль']
                    elif branch_id == 3 and 'Борисов' in credentials:
                        login = credentials['Борисов']['логин']
                        password = credentials['Борисов']['пароль']
                    elif branch_id == 4 and 'Новополоцк' in credentials:
                        login = credentials['Новополоцк']['логин']
                        password = credentials['Новополоцк']['пароль']
                    
                    if login and password and crm_id and name:
                        user_crm_name_splitted = name.split(" ")[:2]
                        user_crm_name_full = " ".join(user_crm_name_splitted).strip()
                        # кибероны из киберклаба
                        kiberons = get_kiberons_count(crm_id, user_crm_name_full, login, password)
                        self.stdout.write(self.style.SUCCESS(f'Получено {kiberons} кликоинов из личного кабинета для {name}'))
                        
                        # Получаем текущий баланс киберонов из CRM
                        crm_kiberons = get_client_kiberons(branch_id, crm_id)
                        self.stdout.write(self.style.SUCCESS(f'Текущий баланс кликоинов в CRM для {name}: {crm_kiberons}'))
                        
                        if kiberons is not None and crm_kiberons is not None:
                            try:
                                current_crm_kiberons = int(crm_kiberons) if crm_kiberons is not None else 0
                            except ValueError:
                                self.stdout.write(self.style.ERROR(f'Ошибка конвертации current_crm_kiberons значений для {name}'))
                                continue
                            
                            try:
                                kiberons = int(kiberons)
                            except ValueError:
                                self.stdout.write(self.style.ERROR(f'Ошибка конвертации kiberons значений для {name}'))
                                continue
                            
                            # Проверяем, нужно ли обновлять киберонов
                            if current_crm_kiberons < kiberons:
                                # Обновляем количество киберонов в CRM
                                crm_result = set_client_kiberons(branch_id, crm_id, kiberons)
                                
                                if crm_result:
                                    self.stdout.write(self.style.SUCCESS(f'Киберонов обновлено в CRM для {name}: с {current_crm_kiberons} до {kiberons}'))
                                else:
                                    self.stdout.write(self.style.ERROR(f'Ошибка обновления в CRM для {name}'))
                                    with open(output_file, 'a', newline='', encoding='utf-8') as csvfile:
                                        fieldnames = ['name', 'branch_id', 'balance', 'crm_update_status']
                                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                                        writer.writerow({
                                            'name': name,
                                            'branch_id': branch_id,
                                            'balance': kiberons,
                                            'crm_update_status': f'ошибка: {e}'
                                        })
                            else:
                                self.stdout.write(self.style.SUCCESS(f'Кибероны не обновлены в CRM для {name}: ЦРМ {current_crm_kiberons} ЛК {kiberons}'))

                        else:
                            self.stdout.write(self.style.WARNING(f'Не удалось получить данные для {name}'))
                            # Записываем данные в CSV для клиентов с ошибками
                            with open(output_file, 'a', newline='', encoding='utf-8') as csvfile:
                                fieldnames = ['name', 'branch_id', 'balance', 'crm_update_status']
                                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                                writer.writerow({
                                    'name': name,
                                    'branch_id': branch_id,
                                    'balance': kiberons if kiberons else 0,
                                })
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Ошибка при обработке клиента {name}: {e}'))
                    # Записываем данные в CSV для клиентов с ошибками
                    with open(output_file, 'a', newline='', encoding='utf-8') as csvfile:
                        fieldnames = ['name', 'branch_id', 'balance', 'crm_update_status']
                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writerow({
                            'name': name,
                            'branch_id': branch_id,
                            'balance': kiberons if kiberons else 0,
                        })
                    self.stdout.write(f'Обработан клиент с ошибкой {name}: {kiberons} киберонов, статус: ошибка: {e}')
            
            self.stdout.write(self.style.SUCCESS(f'Результаты сохранены в {output_file}'))
