"""Deduplicate Telegram service messages."""

import sqlalchemy as sa

from alembic import op

revision = "0002_security_message_unique"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    indexes = {
        item["name"]: item for item in sa.inspect(bind).get_indexes("security_messages")
    }
    current = indexes.get("uq_security_messages_account_tg_msg")
    if current and current.get("unique"):
        return
    if current:
        op.drop_index("uq_security_messages_account_tg_msg", table_name="security_messages")
    op.create_index(
        "uq_security_messages_account_tg_msg",
        "security_messages",
        ["account_id", "tg_msg_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_security_messages_account_tg_msg", table_name="security_messages")
