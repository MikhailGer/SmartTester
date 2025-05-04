# SmartTester

**SmartTester** — это модульная система для записи, анализа и автоматического воспроизведения пользовательских действий в браузере. Проект поддерживает масштабируемое выполнение тестов через Celery, логгирование действий, воспроизведение с использованием координатных и селекторных стратегий, а также имеет инфраструктуру для хранения и анализа результатов.

---

## 🚀 Возможности

- 🔁 Воспроизведение действий пользователя на страницах браузера (включая Shadow DOM, iframe, координаты, прокрутку, drag-n-drop и др.).
- 📦 Docker-окружение для изоляции.
- 🧵 Асинхронные задачи через Celery + Redis.
- 🧩 Поддержка реплеев с fallback-механизмами навигации.
- 🧪 Инфраструктура для логирования, хранения и анализа неудачных тестов.
- 📚 Расширяемая архитектура на базе FastAPI и PostgreSQL.
- 🛠️ Миграции базы данных через Alembic.

---

## 🗂 Структура проекта

```
SmartTester/
│
├── docker-compose.yml        # Контейнеризация Redis, PostgreSQL и приложения
├── Dockerfile                # Docker для FastAPI приложения
├── requirements.txt          # Зависимости Python
├── .env                      # Переменные среды (не коммитится)
├── .env_example              # Пример для настройки .env
│
├── main.py                   # Точка входа в FastAPI-приложение
├── JSON_sorter.py            # Утилита для сортировки логов (нужно уточнение)
├── replay_fails/             # Логи неудачных воспроизведений
├── log_examples/             # Примеры логов
├── scratch/                  # Черновики и временные файлы
│
├── alembic.ini               # Конфигурация миграций Alembic
├── alembic/                  # Скрипты миграций базы данных
│
└── src/                      # Исходный код приложения
    ├── celery_app.py         # Конфигурация Celery
    ├── config.py             # Конфигурация из .env
    ├── crud.py               # Доступ к данным
    ├── db.py                 # Подключение к БД
    ├── models.py             # SQLAlchemy модели
    ├── schemas.py            # Pydantic-схемы
    ├── tasks.py              # Задачи Celery
    ├── replayer.py           # Основной реплеер действий
    └── replayer_new.py       # Альтернативный или тестовый реплеер
```

---

## ⚙️ Установка и запуск

### 1. Клонировать репозиторий:
```bash
git clone https://github.com/your-username/SmartTester.git
cd SmartTester
```

### 2. Настроить окружение:
```bash
cp .env_example .env
# отредактируйте .env при необходимости
```

### 3. Собрать и запустить через Docker:
```bash
docker-compose up --build
```

### 4. Выполнить миграции БД:
```bash
docker-compose exec app alembic upgrade head
```

---

## 🧪 Тестирование реплеев

Добавьте JSON-логи пользовательских действий в соответствующую папку, затем используйте один из `replayer.py` скриптов для воспроизведения:

```bash
python src/replayer.py path/to/log.json
```

---

## 📌 Используемые технологии

- Python 3.10+
- FastAPI
- SQLAlchemy + Alembic
- PostgreSQL
- Redis
- Celery
- Docker / Docker Compose
- Selenium / undetected_chromedriver

---

