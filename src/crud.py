from datetime import datetime
from typing import List, Any, Optional
from sqlalchemy.orm import Session

import src.models, src.schemas
from src.models import StatusEnum


# --- Proxy CRUD ---

def create_proxy(db: Session, proxy_in: src.schemas.ProxyCreate) -> src.models.Proxy:
    proxy = src.models.Proxy(**proxy_in.dict())
    db.add(proxy)
    db.commit()
    db.refresh(proxy)
    return proxy


def get_proxy(db: Session, proxy_id: int) -> Optional[src.models.Proxy]:
    return db.get(src.models.Proxy, proxy_id)


def list_proxies(db: Session) -> List[src.models.Proxy]:
    return db.query(src.models.Proxy).all()


# --- FarmTask CRUD ---

def create_farm_task(
    db: Session,
    farm_in: src.schemas.FarmTaskCreate,
    proxy_id: int
) -> src.models.FarmTask:
    task = src.models.FarmTask(
        instructions=farm_in.instructions,
        assigned_proxy_id=proxy_id
    )
    # task = src.models.FarmTask(
    #     target_url=str(farm_in.target_url),
    #     instructions=farm_in.instructions,
    #     assigned_proxy_id=proxy_id
    # )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_farm_task(db: Session, task_id: int) -> Optional[src.models.FarmTask]:
    return db.get(src.models.FarmTask, task_id)


def get_pending_farm(db: Session) -> List[src.models.FarmTask]:
    return (
        db.query(src.models.FarmTask)
          .filter(src.models.FarmTask.status == StatusEnum.pending)
          .order_by(src.models.FarmTask.created_at)
          .all()
    )


def update_farm_task_status(
    db: Session,
    task: src.models.FarmTask,
    status: StatusEnum,
    completed_at: Optional[datetime] = None,
    error: Optional[str] = None
) -> src.models.FarmTask:
    task.status = status
    if completed_at is not None:
        task.completed_at = completed_at
    if error is not None:
        task.error = error
    db.commit()
    db.refresh(task)
    return task


# --- UserSession CRUD ---

def create_user_session(
    db: Session,
    farm_task: src.models.FarmTask,
    cookies: List[dict],
    user_agent: str,
    expires_at: Optional[datetime] = None
) -> src.models.UserSession:
    us = src.models.UserSession(
        farm_task_id=farm_task.id,
        proxy_id=farm_task.assigned_proxy_id,
        cookies=cookies,
        user_agent=user_agent,
        expires_at=expires_at
    )
    db.add(us)
    db.commit()
    db.refresh(us)
    return us


def get_user_session(db: Session, session_id: int) -> Optional[src.models.UserSession]:
    return db.get(src.models.UserSession, session_id)


def list_user_sessions(db: Session) -> List[src.models.UserSession]:
    return db.query(src.models.UserSession).all()


# --- JobTask CRUD ---

def create_job_task(
    db: Session,
    job_in: src.schemas.JobTaskCreate,
    session_id: int
) -> src.models.JobTask:
    jt = src.models.JobTask(
        session_id=session_id,
        instructions=job_in.instructions
    )
    db.add(jt)
    db.commit()
    db.refresh(jt)
    return jt


def get_job_task(db: Session, job_id: int) -> Optional[src.models.JobTask]:
    return db.get(src.models.JobTask, job_id)


def get_pending_jobs(db: Session) -> List[src.models.JobTask]:
    return (
        db.query(src.models.JobTask)
          .filter(src.models.JobTask.status == StatusEnum.pending)
          .order_by(src.models.JobTask.created_at)
          .all()
    )


def update_job_task_status(
    db: Session,
    job: src.models.JobTask,
    status: StatusEnum,
    completed_at: Optional[datetime] = None,
    error: Optional[str] = None
) -> src.models.JobTask:
    job.status = status
    if completed_at is not None:
        job.completed_at = completed_at
    if error is not None:
        job.error = error
    db.commit()
    db.refresh(job)
    return job


# --- JobReport CRUD ---

def create_job_report(
    db: Session,
    job_task: src.models.JobTask,
    status_code: Optional[int] = None,
    result_text: Optional[str] = None,
    report_metadata: Any = None,
    error: Optional[str] = None
) -> src.models.JobReport:
    jr = src.models.JobReport(
        job_task_id=job_task.id,
        status_code=status_code,
        result_text=result_text,
        report_metadata=report_metadata,
        error=error
    )
    db.add(jr)
    db.commit()
    db.refresh(jr)
    return jr


def get_reports_by_job(db: Session, job_id: int) -> List[src.models.JobReport]:
    return db.query(src.models.JobReport).filter(src.models.JobReport.job_task_id == job_id).all()
