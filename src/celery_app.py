from celery import Celery
from src.config import settings

# Инициализация Celery с использованием RabbitMQ из .env
# Убедитесь, что в .env задана переменная RABBITMQ_URL, например:
# RABBITMQ_URL=amqp://user:password@localhost:5672/

broker_url = settings.RABBITMQ_URL
if not broker_url:
    raise RuntimeError("RABBITMQ_URL is not configured in .env")

# Используем RabbitMQ также как бэкенд результатов или можно оставить Redis
result_backend = settings.RABBITMQ_URL

celery_app = Celery(
    'behaviorfarm',
    broker=broker_url,
    backend=None,
)

# Дополнительная конфигурация Celery (по необходимости)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    # При большом количестве задач можно задать маршрутизацию
    # task_routes = {
    #     'tasks.farm_cookie': {'queue': 'farm_queue'},
    #     'tasks.run_job':    {'queue': 'job_queue'},
    # }
)

# Автоматически импортируем задачи из модуля tasks.py
celery_app.autodiscover_tasks(['src.tasks'])
