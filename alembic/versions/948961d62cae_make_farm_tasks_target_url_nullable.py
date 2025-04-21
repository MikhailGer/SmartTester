from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '948961d62cae'
down_revision = '2f671ab03fac'
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column(
        'farm_tasks',
        'target_url',
        existing_type=sa.String(),  # или тот тип, что у вас
        nullable=True,
    )

def downgrade():
    op.alter_column(
        'farm_tasks',
        'target_url',
        existing_type=sa.String(),
        nullable=False,
    )
