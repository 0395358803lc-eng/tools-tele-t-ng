"""Legacy PostgreSQL baseline."""

import sqlalchemy as sa

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("first_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("username", sa.String(64), nullable=False, server_default=""),
        sa.Column("bio", sa.String(140), nullable=False, server_default=""),
        sa.Column("session_file", sa.String(255), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="disconnected"
        ),
        sa.Column("has_2fa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("phone", name="uq_accounts_phone"),
    )
    op.create_index("ix_accounts_phone", "accounts", ["phone"])
    op.create_table(
        "security_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tg_msg_id", sa.BigInteger(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "received_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "gone_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("first_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("username", sa.String(64), nullable=False, server_default=""),
        sa.Column("old_serial", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(16), nullable=False, server_default="removed"),
        sa.Column(
            "gone_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_gone_accounts_phone", "gone_accounts", ["phone"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_table(
        "pending_logins",
        sa.Column("phone", sa.String(32), primary_key=True),
        sa.Column("phone_code_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("pending_logins")
    op.drop_table("app_settings")
    op.drop_index("ix_gone_accounts_phone", table_name="gone_accounts")
    op.drop_table("gone_accounts")
    op.drop_table("security_messages")
    op.drop_index("ix_accounts_phone", table_name="accounts")
    op.drop_table("accounts")
