"""upstream_servers: the tools only admins may use (`admin_only_tools`)

The gateway hides these from members' tool lists and refuses their calls
(gateway/app/tools.py). WooCommerce's write tools start locked on every
store's server, so members can read orders and products but not change them.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'upstream_servers',
        sa.Column('admin_only_tools', postgresql.ARRAY(sa.String()), server_default='{}', nullable=False),
    )
    # The stores that exist already: woo-<subdomain>, as app/services/stores.py WOO_ADMIN_ONLY_TOOLS gives new ones.
    op.execute(
        "UPDATE upstream_servers SET admin_only_tools = ARRAY['create_product', 'update_product', 'update_order_status']"
        " WHERE slug LIKE 'woo-%'"
    )


def downgrade() -> None:
    op.drop_column('upstream_servers', 'admin_only_tools')
