"""Persist encrypted Telegram session snapshots in PostgreSQL."""

import sqlalchemy as sa

from alembic import op

revision = "0005_server_portable_sessions"
down_revision = "0004_remove_pending_logins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "telegram_session_blobs" not in inspector.get_table_names():
        op.create_table(
            "telegram_session_blobs",
            sa.Column(
                "account_id",
                sa.Integer(),
                sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "telegram_session_blobs" in inspector.get_table_names():
        op.drop_table("telegram_session_blobs")
