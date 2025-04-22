from datetime import datetime
from typing import List, Any, Optional
from sqlalchemy.orm import Session

import src.models, src.schemas
from src.models import (
    StatusEnum,
    InstructionSet,
    InstructionType,
    FarmTask,
    JobTask,
)


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
) -> FarmTask:
    # Проверяем наличие набора инструкций и его тип
    instr_set = db.get(InstructionSet, farm_in.instruction_set_id)
    if not instr_set:
        raise ValueError(f"InstructionSet {farm_in.instruction_set_id} not found")
    if instr_set.type != InstructionType.farm:
        raise ValueError(f"InstructionSet {instr_set.id} is not of type 'farm'")

    task = FarmTask(
        instruction_set_id=instr_set.id,
        assigned_proxy_id=proxy_id
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_farm_task(db: Session, task_id: int) -> Optional[FarmTask]:
    return db.get(FarmTask, task_id)


def get_pending_farm(db: Session) -> List[FarmTask]:
    return (
        db.query(FarmTask)
          .filter(FarmTask.status == StatusEnum.pending)
          .order_by(FarmTask.created_at)
          .all()
    )


def update_farm_task_status(
    db: Session,
    task: FarmTask,
    status: StatusEnum,
    completed_at: Optional[datetime] = None,
    error: Optional[str] = None
) -> FarmTask:
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
    farm_task: FarmTask,
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
) -> JobTask:
    # Проверяем наличие набора инструкций и его тип
    instr_set = db.get(InstructionSet, job_in.instruction_set_id)
    if not instr_set:
        raise ValueError(f"InstructionSet {job_in.instruction_set_id} not found")
    if instr_set.type != InstructionType.job:
        raise ValueError(f"InstructionSet {instr_set.id} is not of type 'job'")

    jt = JobTask(
        session_id=session_id,
        instruction_set_id=instr_set.id
    )
    db.add(jt)
    db.commit()
    db.refresh(jt)
    return jt


def get_job_task(db: Session, job_id: int) -> Optional[JobTask]:
    return db.get(JobTask, job_id)


def get_pending_jobs(db: Session) -> List[JobTask]:
    return (
        db.query(JobTask)
          .filter(JobTask.status == StatusEnum.pending)
          .order_by(JobTask.created_at)
          .all()
    )


def update_job_task_status(
    db: Session,
    job: JobTask,
    status: StatusEnum,
    completed_at: Optional[datetime] = None,
    error: Optional[str] = None
) -> JobTask:
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
    job_task: JobTask,
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

# --- InstructionSet CRUD ---
def create_instruction_set(
    db: Session,
    inst_in: src.schemas.InstructionSetCreate
) -> src.models.InstructionSet:
    inst = src.models.InstructionSet(
        name=inst_in.name,
        type=inst_in.type,
        instructions=inst_in.instructions
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def list_instruction_sets(
    db: Session
) -> list[src.models.InstructionSet]:
    return db.query(src.models.InstructionSet).order_by(src.models.InstructionSet.created_at).all()


def get_instruction_set(
    db: Session,
    inst_id: int
) -> src.models.InstructionSet | None:
    return db.get(src.models.InstructionSet, inst_id)
