import time, subprocess, atexit
from datetime import datetime

from src.celery_app import celery_app
from src.config import get_db
import src.crud, src.models, src.replayer_new


def start_local_proxy(upstream_proxy: str) -> str:
    """
    Запускает proxy.py в режиме форвардера с ProxyPoolPlugin
    upstream_proxy вида "http://user:pass@host:port" или "http://host:port"
    Возвращает строку "127.0.0.1:<free_port>"
    """
    port = src.replayer_new._find_free_port()
    # Собираем команду
    cmd = [
        "proxy",
        "--hostname", "127.0.0.1",
        "--port", str(port),
        "--plugins", "proxy.plugin.proxy_pool.ProxyPoolPlugin",
        "--proxy-pool", upstream_proxy,
        "--threaded",
    ]
    proc = subprocess.Popen(cmd)
    # гарантируем, что при завершении процесса таска форвардер тоже упадёт
    atexit.register(lambda: proc.terminate())
    # даём ему немного времени, чтобы подняться
    time.sleep(0.5)
    return f"127.0.0.1:{port}"


@celery_app.task(name="farm_cookie")
def farm_cookie(task_id: int, base_session_id: int | None = None, skip_substrings: list[str] | None = None,
                inplace: bool = False):
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
    p = farm.proxy

    p = farm.proxy

    if p.login and p.password:
        upstream = f"{p.type}://{p.login}:{p.password}@{p.ip}:{p.port}"
    else:
        upstream = f"{p.type}://{p.ip}:{p.port}"

    local_proxy = start_local_proxy(upstream)

    try:
        cookie, user_agent = src.replayer_new.replay_events(
            events,
            skip_substrings=set(skip_substrings or []),
            user_agent=base_ua,
            cookies=base_cookies,
            proxy=local_proxy
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

    except Exception as e:
        src.crud.update_farm_task_status(db, farm, src.models.StatusEnum.failed, error=str(e),
                                         completed_at=datetime.utcnow())
        return f"FarmTask {task_id} failed with error {e}"


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
    src.replayer_new.replay_events(
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
