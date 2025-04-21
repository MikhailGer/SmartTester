from datetime import datetime
from typing import Optional, Any, List
from pydantic import BaseModel, HttpUrl


class ProxyCreate(BaseModel):
    ip: str
    port: int
    login: Optional[str]
    password: Optional[str]
    country: Optional[str]
    type: Optional[str] = "http"


class ProxyRead(ProxyCreate):
    id: int
    is_working: bool
    last_checked: Optional[datetime]

    class Config:
        orm_mode = True


class FarmTaskCreate(BaseModel):
    # target_url: HttpUrl
    instructions: Any  # JSON-структура сценария


class FarmTaskRead(BaseModel):
    id: int
    # target_url: HttpUrl
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]

    class Config:
        orm_mode = True


class UserSessionRead(BaseModel):
    id: int
    proxy_id: int
    cookies: Any    # JSON-массив куки
    user_agent: str
    created_at: datetime
    expires_at: Optional[datetime]

    class Config:
        orm_mode = True


class JobTaskCreate(BaseModel):
    instructions: Any


class JobTaskRead(BaseModel):
    id: int
    session_id: int
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]

    class Config:
        orm_mode = True


class JobReportRead(BaseModel):
    id: int
    job_task_id: int
    status_code: Optional[int]
    result_text: Optional[str]
    report_metadata: Optional[Any]
    error: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True
