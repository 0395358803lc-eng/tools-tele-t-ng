"""Add mutation audit log."""

import sqlalchemy as sa

from alembic import op

revision = "0003_audit_logs"
down_revision = "0002_security_message_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" not in inspector.get_table_names():
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("method", sa.String(8), nullable=False),
            sa.Column("path", sa.String(255), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("client_ip", sa.String(64), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("audit_logs")}
    if "ix_audit_logs_created_at" not in indexes:
        op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
