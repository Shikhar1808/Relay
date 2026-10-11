"""upstream MCP servers the gateway forwards to

Credentials are stored only as AES-256-GCM ciphertext. store_id NULL means
the server is shared by everyone; deleting a store removes its servers.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('upstream_servers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slug', sa.String(length=50), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('credential_header', sa.String(length=100), nullable=False),
    sa.Column('credential_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('store_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_upstream_servers_slug'), 'upstream_servers', ['slug'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_upstream_servers_slug'), table_name='upstream_servers')
    op.drop_table('upstream_servers')
