from src.celery_app import celery_app
from src.config import get_db
import src.crud, src.models, src.replayer

@celery_app.task(name="farm_cookie")
def farm_cookie(task_id: int):
    db = next(get_db())
    farm = src.crud.get_farm_task(db, task_id)
    if not farm:
        return f"FarmTask {task_id} not found"

    # Обновляем статус задачи
    src.crud.update_farm_task_status(
        db,
        farm,
        status=src.models.StatusEnum.processing
    )

    # Реплей фарминга
    events = farm.instructions
    src.replayer.replay_events(
        events,
        user_agent=None,
        cookies=None,
        proxy=None
    )

    # Сохраняем UserSession
    us = src.crud.create_user_session(
        db,
        farm_task=farm,
        cookies=[],  # TODO: заменить на реальные cookies после реплея
        user_agent=""
    )
    src.crud.update_farm_task_status(
        db,
        farm,
        status=src.models.StatusEnum.success
    )
    return f"Created UserSession {us.id} for FarmTask {task_id}"

@celery_app.task(name="run_job")
def run_job(job_id: int):
    db = next(get_db())
    job = src.crud.get_job_task(db, job_id)
    if not job:
        return f"JobTask {job_id} not found"

    # Обновляем статус задачи
    src.crud.update_job_task_status(
        db,
        job,
        status=src.models.StatusEnum.processing
    )

    # Реплей боевого сценария
    events = job.instructions
    src.replayer.replay_events(
        events,
        user_agent=job.session.user_agent,
        cookies=job.session.cookies,
        proxy=None
    )

    # Создаем отчет
    src.crud.create_job_report(
        db,
        job_task=job,
        status_code=200,
        result_text="OK"
    )
    src.crud.update_job_task_status(
        db,
        job,
        status=src.models.StatusEnum.success
    )
    return f"JobTask {job_id} completed"
