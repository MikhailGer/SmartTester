from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    ForeignKey, JSON, Text, Enum, Index
)
from sqlalchemy.orm import relationship
import enum

# Импортируем Base из корневого пакета src
from src.config import Base


class InstructionType(str, enum.Enum):
    farm = "farm"
    job = "job"


class InstructionSet(Base):
    __tablename__ = "instruction_sets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    type = Column(Enum(InstructionType), nullable=False)
    instructions = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StatusEnum(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    success = "success"
    failed = "failed"


class Proxy(Base):
    __tablename__ = "proxies"
    __table_args__ = (
        Index('ix_proxies_is_working_last_checked', 'is_working', 'last_checked'),
    )

    id = Column(Integer, primary_key=True)
    ip = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    login = Column(String)
    password = Column(String)
    country = Column(String)
    type = Column(String, default="http")  # HTTP, HTTPS, SOCKS5
    is_working = Column(Boolean, default=True)
    last_checked = Column(DateTime)

    farm_tasks = relationship("FarmTask", back_populates="proxy")
    user_sessions = relationship("UserSession", back_populates="proxy")


class FarmTask(Base):
    __tablename__ = "farm_tasks"
    __table_args__ = (
        Index('ix_farm_tasks_status_created', 'status', 'created_at'),
    )

    id = Column(Integer, primary_key=True)
    target_url = Column(String, nullable=False)
    # instructions = Column(JSON, nullable=False)
    instruction_set_id = Column(
        Integer,
        ForeignKey("instruction_sets.id", ondelete="RESTRICT"),
        nullable=False
    )
    assigned_proxy_id = Column(Integer, ForeignKey("proxies.id"), nullable=False)
    status = Column(Enum(StatusEnum), default=StatusEnum.pending, nullable=False)
    error = Column(Text)
    attempts_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)

    instruction_set = relationship("InstructionSet")
    proxy = relationship("Proxy", back_populates="farm_tasks")
    user_session = relationship("UserSession", back_populates="farm_task", uselist=False)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index('ix_user_sessions_expires', 'expires_at'),
    )

    id = Column(Integer, primary_key=True)
    farm_task_id = Column(Integer, ForeignKey("farm_tasks.id"), unique=True)
    proxy_id = Column(Integer, ForeignKey("proxies.id"), nullable=False)
    cookies = Column(JSON, nullable=False)
    user_agent = Column(String, nullable=False)
    fingerprint = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime)

    proxy = relationship("Proxy", back_populates="user_sessions")
    farm_task = relationship("FarmTask", back_populates="user_session")
    job_tasks = relationship("JobTask", back_populates="session")


class JobTask(Base):
    __tablename__ = "job_tasks"
    __table_args__ = (
        Index('ix_job_tasks_status_created', 'status', 'created_at'),
    )

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("user_sessions.id"), nullable=False)
    # instructions = Column(JSON, nullable=False)
    instruction_set_id = Column(
        Integer,
        ForeignKey("instruction_sets.id", ondelete="RESTRICT"),
        nullable=False
    )
    status = Column(Enum(StatusEnum), default=StatusEnum.pending, nullable=False)
    error = Column(Text)
    attempts_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)

    instruction_set = relationship("InstructionSet")
    session = relationship("UserSession", back_populates="job_tasks")
    reports = relationship("JobReport", back_populates="job_task")


class JobReport(Base):
    __tablename__ = "job_reports"
    __table_args__ = (
        Index('ix_job_reports_job_task_created', 'job_task_id', 'created_at'),
    )

    id = Column(Integer, primary_key=True)
    job_task_id = Column(Integer, ForeignKey("job_tasks.id"), nullable=False)
    status_code = Column(Integer)
    result_text = Column(Text)
    report_metadata = Column(JSON)  # переименовано из metadata
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    job_task = relationship("JobTask", back_populates="reports")
