"""empty message

Revision ID: 296dd6949985
Revises: 948961d62cae
Create Date: 2025-04-22 23:26:48.896193

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '296dd6949985'
down_revision: Union[str, None] = '948961d62cae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # 1) создаём таблицу instruction_sets
    op.create_table(
        "instruction_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("type",
                  postgresql.ENUM("farm","job", name="instructiontype"),
                  nullable=False),
        sa.Column("instructions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    # 2) добавляем FK к farm_tasks и job_tasks
    op.add_column("farm_tasks",
        sa.Column("instruction_set_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_farm_tasks_instruction",
        "farm_tasks", "instruction_sets",
        ["instruction_set_id"], ["id"],
        ondelete="RESTRICT"
    )
    op.add_column("job_tasks",
        sa.Column("instruction_set_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_job_tasks_instruction",
        "job_tasks", "instruction_sets",
        ["instruction_set_id"], ["id"],
        ondelete="RESTRICT"
    )

    # 3) перенести существующие JSON‑инструкции в новую таблицу
    # (это можно сделать скриптом на Python либо вручную, если немного данных)

    # 4) сделать поля NOT NULL
    op.alter_column("farm_tasks", "instruction_set_id", nullable=False)
    op.alter_column("job_tasks",  "instruction_set_id", nullable=False)

    # 5) (опционально) удалить старые столбцы JSON instructions
    op.drop_column("farm_tasks", "instructions")
    op.drop_column("job_tasks",  "instructions")


def downgrade():
    # обратная последовательность: добавить колонки, сбросить FK, удалить таблицу
    op.add_column("farm_tasks", sa.Column("instructions", sa.JSON(), nullable=False))
    op.add_column("job_tasks",  sa.Column("instructions", sa.JSON(), nullable=False))
    op.drop_constraint("fk_farm_tasks_instruction", "farm_tasks", type_="foreignkey")
    op.drop_constraint("fk_job_tasks_instruction",  "job_tasks",  type_="foreignkey")
    op.drop_column("farm_tasks", "instruction_set_id")
    op.drop_column("job_tasks",  "instruction_set_id")
    op.drop_table("instruction_sets")
    # и удалить тип ENUM, если нужно