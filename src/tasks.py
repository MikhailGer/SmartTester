from src.celery_app import celery_app
from src.config import get_db
import src.crud, src.models, src.replayer


@celery_app.task(name="farm_cookie")
def farm_cookie(task_id: int, base_session_id: int | None = None, skip_substrings: list[str] | None = None, inplace: bool = False):
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

    base_cookies, base_ua = (None, None)
    if base_session_id:
        base_sess = src.crud.get_user_session(db, base_session_id)
        if not base_sess:
            raise ValueError(f"Base session {base_session_id} not found")
        base_cookies, base_ua = base_sess.cookies, base_sess.user_agent

    src.crud.update_farm_task_status(db, farm, src.models.StatusEnum.processing)

    # Реплей фарминга
    inst_set = farm.instruction_set
    events = inst_set.instructions

    cookie, user_agent = src.replayer.replay_events(
        events,
        skip_substrings=set(skip_substrings or []),
        user_agent=base_ua,
        cookies=base_cookies,
        proxy=None
    )

    if inplace and base_session_id:
        sess = src.crud.get_user_session(db, base_session_id)
        us = src.crud.update_user_session(
            db,
            sess,
            cookies=cookie,
            user_agent=user_agent,
        )
    else:
        # иначе — создаём новую
        us = src.crud.create_user_session(
            db,
            farm_task=farm,
            cookies=cookie,
            user_agent=user_agent,
            parent_session_id=base_session_id
        )

    src.crud.update_farm_task_status(
        db,
        farm,
        status=src.models.StatusEnum.success
    )
    return f"Created UserSession {us.id} for FarmTask {task_id}"


@celery_app.task(name="run_job")
def run_job(job_id: int, skip_substrings: list[str] | None = None):
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
    inst_set = job.instruction_set
    events = inst_set.instructions
    src.replayer.replay_events(
        events,
        skip_substrings=set(skip_substrings or []),
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
