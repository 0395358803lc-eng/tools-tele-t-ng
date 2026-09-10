"""Persist remembered Telegram 2FA passwords in PostgreSQL."""

import sqlalchemy as sa

from alembic import op

revision = "0006_remembered_twofa_sql"
down_revision = "0005_server_portable_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "remembered_twofa" not in inspector.get_table_names():
        op.create_table(
            "remembered_twofa",
            sa.Column("phone", sa.String(32), primary_key=True),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "remembered_twofa" in inspector.get_table_names():
        op.drop_table("remembered_twofa")
