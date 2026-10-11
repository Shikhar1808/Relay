"""admin_events: an append-only record of team, API key and store changes

A trigger refuses UPDATE, DELETE and TRUNCATE for every role, the owner
included, so a row can only ever be added.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('admin_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=False),
    sa.Column('actor_email', sa.String(length=320), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('target', sa.String(length=320), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('client_ip', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    # One statement per execute: asyncpg refuses several in one.
    op.execute(
        "CREATE FUNCTION admin_events_is_append_only() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'admin_events is append-only'; END $$"
    )
    op.execute(
        "CREATE TRIGGER admin_events_no_change BEFORE UPDATE OR DELETE ON admin_events "
        "FOR EACH ROW EXECUTE FUNCTION admin_events_is_append_only()"
    )
    op.execute(
        "CREATE TRIGGER admin_events_no_truncate BEFORE TRUNCATE ON admin_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION admin_events_is_append_only()"
    )


def downgrade() -> None:
    op.drop_table('admin_events')  # its triggers go with it
    op.execute("DROP FUNCTION admin_events_is_append_only()")
