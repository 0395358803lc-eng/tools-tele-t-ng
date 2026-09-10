"""Remove unused pending login persistence table."""

import sqlalchemy as sa

from alembic import op

revision = "0004_remove_pending_logins"
down_revision = "0003_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "pending_logins" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("pending_logins")


def downgrade() -> None:
    op.create_table(
        "pending_logins",
        sa.Column("phone", sa.String(32), primary_key=True),
        sa.Column("phone_code_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
