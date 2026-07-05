"""integration api keys

Revision ID: 6f60f08dbf55
Revises: 18cddf7195d2
Create Date: 2026-07-05 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6f60f08dbf55"
down_revision: Union[str, None] = "18cddf7195d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("integration_clients") as batch_op:
        batch_op.add_column(sa.Column("key_prefix", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        sa.text(
            "UPDATE integration_clients SET key_prefix = substr(client_id, 1, 12) WHERE key_prefix IS NULL"
        )
    )

    with op.batch_alter_table("integration_clients") as batch_op:
        batch_op.alter_column("key_prefix", existing_type=sa.String(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_integration_clients_key_prefix"),
            ["key_prefix"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("integration_clients") as batch_op:
        batch_op.drop_index(batch_op.f("ix_integration_clients_key_prefix"))
        batch_op.drop_column("last_used_at")
        batch_op.drop_column("key_prefix")
