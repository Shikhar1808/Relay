"""stores: the worker's progress (`step`, `error`) and the status `deleting`

Stores are now created and deleted from the dashboard by the store worker
(app/worker.py), which records here what it is doing and why it last failed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE store_status ADD VALUE IF NOT EXISTS 'deleting'")
    op.add_column('stores', sa.Column('step', sa.String(length=100), nullable=True))
    op.add_column('stores', sa.Column('error', sa.Text(), nullable=True))


def downgrade() -> None:
    # Postgres cannot drop one value from an enum; `deleting` stays in the type, unused.
    op.drop_column('stores', 'error')
    op.drop_column('stores', 'step')
