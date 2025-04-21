from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from src.config import get_db, engine
import src.models, src.crud, src.schemas
from src.models import Base, StatusEnum



# Создаем все таблицы при запуске (MVP)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BehaviorFarm API", version="0.1.0")

# --- Proxy Endpoints ---
@app.post("/proxies/", response_model=src.schemas.ProxyRead)
def create_proxy(
    proxy_in: src.schemas.ProxyCreate,
    db: Session = Depends(get_db)
) -> src.models.Proxy:
    return src.crud.create_proxy(db, proxy_in)

@app.get("/proxies/", response_model=list[src.schemas.ProxyRead])
def list_proxies(db: Session = Depends(get_db)) -> list[src.models.Proxy]:
    return src.crud.list_proxies(db)

# --- FarmTask Endpoints ---
@app.post("/farm_tasks/", response_model=src.schemas.FarmTaskRead)
def create_farm_task(
    farm_in: src.schemas.FarmTaskCreate,
    proxy_id: int,
    db: Session = Depends(get_db)
) -> src.models.FarmTask:
    if not src.crud.get_proxy(db, proxy_id):
        raise HTTPException(status_code=404, detail="Proxy not found")
    return src.crud.create_farm_task(db, farm_in, proxy_id)

@app.get("/farm_tasks/pending", response_model=list[src.schemas.FarmTaskRead])
def pending_farm_tasks(db: Session = Depends(get_db)) -> list[src.models.FarmTask]:
    return src.crud.get_pending_farm(db)

@app.post("/farm_tasks/{task_id}/run")
def run_farm_task(task_id: int, db: Session = Depends(get_db)):
    task = src.crud.get_farm_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="FarmTask not found")
    # Запланировать Celery-задачу (псевдокод)
    # farm_cookie.delay(task_id)
    return {"message": "Farm task scheduled", "task_id": task_id}

# --- UserSession Endpoints ---
@app.get("/user_sessions/", response_model=list[src.schemas.UserSessionRead])
def list_user_sessions(db: Session = Depends(get_db)) -> list[src.models.UserSession]:
    return src.crud.list_user_sessions(db)

# --- JobTask Endpoints ---
@app.post("/job_tasks/", response_model=src.schemas.JobTaskRead)
def create_job_task(
    job_in: src.schemas.JobTaskCreate,
    session_id: int,
    db: Session = Depends(get_db)
) -> src.models.JobTask:
    if not src.crud.get_user_session(db, session_id):
        raise HTTPException(status_code=404, detail="UserSession not found")
    return src.crud.create_job_task(db, job_in, session_id)

@app.get("/job_tasks/pending", response_model=list[src.schemas.JobTaskRead])
def pending_job_tasks(db: Session = Depends(get_db)) -> list[src.models.JobTask]:
    return src.crud.get_pending_jobs(db)

@app.post("/job_tasks/{job_id}/run")
def run_job_task(job_id: int, db: Session = Depends(get_db)):
    job = src.crud.get_job_task(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="JobTask not found")
    # Запланировать Celery-задачу (псевдокод)
    # run_job.delay(job_id)
    return {"message": "Job task scheduled", "job_id": job_id}

# --- JobReport Endpoints ---
@app.get("/job_reports/{job_id}", response_model=list[src.schemas.JobReportRead])
def get_job_reports(job_id: int, db: Session = Depends(get_db)) -> list[src.models.JobReport]:
    return src.crud.get_reports_by_job(db, job_id)

# --- Healthcheck ---
@app.get("/health")
def health():
    return {"status": "ok"}
