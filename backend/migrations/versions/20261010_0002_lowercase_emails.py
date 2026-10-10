"""lowercase existing emails

The API now lowercases every email it accepts, so an account stored with a
capital letter could no longer log in. If two accounts differ only by case
this fails on the unique index and nothing changes — merge them by hand,
then run it again.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET email = lower(email) WHERE email <> lower(email)")


def downgrade() -> None:
    pass  # the original capitalisation is not kept anywhere
