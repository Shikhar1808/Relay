"""conversations: one row per chat with the agent; the chat token kept encrypted

The agent's saved state (what was said, tool results) is LangGraph's own. It
creates its tables itself at startup, in the `langgraph` schema made here, so
they stay out of this history and out of reach of the gateway's database role.

chat_tokens.ciphertext holds the token itself, encrypted (app/core/crypto.py):
one token now serves a whole chat, and the backend has to present it again on
every message.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_tokens', sa.Column('ciphertext', sa.LargeBinary(), nullable=True))
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('chat_token_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['chat_token_id'], ['chat_tokens.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chat_token_id')
    )
    op.create_index(op.f('ix_conversations_last_used_at'), 'conversations', ['last_used_at'], unique=False)
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")  # the saved chats go with it
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_last_used_at'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_column('chat_tokens', 'ciphertext')
