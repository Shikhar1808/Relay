"""a member can belong to several stores

Replaces UNIQUE (user_id) on store_members with UNIQUE (store_id, user_id),
so a member can be in many stores but not twice in the same one. Downgrade
fails if any member is now in more than one store: remove the extra
assignments first.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_store_members_one_store_per_user', 'store_members', type_='unique')
    op.create_unique_constraint('uq_store_members_store_user', 'store_members', ['store_id', 'user_id'])


def downgrade() -> None:
    op.drop_constraint('uq_store_members_store_user', 'store_members', type_='unique')
    op.create_unique_constraint('uq_store_members_one_store_per_user', 'store_members', ['user_id'])
