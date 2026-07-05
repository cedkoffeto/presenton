"""provider ready identity and teams

Revision ID: 18cddf7195d2
Revises: c7b70d0f31b1
Create Date: 2026-07-05 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "18cddf7195d2"
down_revision: Union[str, None] = "c7b70d0f31b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("auth_provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("external_user_id", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(
        op.f("ix_users_external_user_id"),
        "users",
        ["external_user_id"],
        unique=False,
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_teams_slug"), "teams", ["slug"], unique=True)

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_team_memberships_user_id"),
        "team_memberships",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_team_memberships_team_id"),
        "team_memberships",
        ["team_id"],
        unique=False,
    )

    op.create_table(
        "integration_clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("secret_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("default_team_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("auto_provision_users", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["default_team_id"], ["teams.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_integration_clients_client_id"),
        "integration_clients",
        ["client_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_integration_clients_default_team_id"),
        "integration_clients",
        ["default_team_id"],
        unique=False,
    )

    op.create_table(
        "themes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("company_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("logo", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("logo_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("visibility", sa.String(), nullable=False),
        sa.Column("share_token", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_themes_owner_user_id"), "themes", ["owner_user_id"], unique=False
    )
    op.create_index(op.f("ix_themes_team_id"), "themes", ["team_id"], unique=False)
    op.create_index(
        op.f("ix_themes_share_token"), "themes", ["share_token"], unique=True
    )

    for table_name in (
        "presentations",
        "templates",
        "imageasset",
        "template_create_infos",
        "async_presentation_generation_tasks",
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("owner_user_id", sa.Uuid(), nullable=True))
            batch_op.add_column(sa.Column("team_id", sa.Uuid(), nullable=True))
            batch_op.add_column(
                sa.Column(
                    "visibility",
                    sa.String(),
                    nullable=False,
                    server_default="private",
                )
            )
            batch_op.create_index(
                batch_op.f(f"ix_{table_name}_owner_user_id"),
                ["owner_user_id"],
                unique=False,
            )
            batch_op.create_index(
                batch_op.f(f"ix_{table_name}_team_id"), ["team_id"], unique=False
            )

    with op.batch_alter_table("presentations") as batch_op:
        batch_op.add_column(sa.Column("share_token", sa.String(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_presentations_share_token"),
            ["share_token"],
            unique=True,
        )

    with op.batch_alter_table("templates") as batch_op:
        batch_op.add_column(sa.Column("share_token", sa.String(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_templates_share_token"),
            ["share_token"],
            unique=True,
        )

    with op.batch_alter_table("presentation_layout_codes") as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("team_id", sa.Uuid(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_presentation_layout_codes_owner_user_id"),
            ["owner_user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_presentation_layout_codes_team_id"),
            ["team_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("presentation_layout_codes") as batch_op:
        batch_op.drop_index(batch_op.f("ix_presentation_layout_codes_team_id"))
        batch_op.drop_index(batch_op.f("ix_presentation_layout_codes_owner_user_id"))
        batch_op.drop_column("team_id")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("templates") as batch_op:
        batch_op.drop_index(batch_op.f("ix_templates_share_token"))
        batch_op.drop_column("share_token")

    with op.batch_alter_table("presentations") as batch_op:
        batch_op.drop_index(batch_op.f("ix_presentations_share_token"))
        batch_op.drop_column("share_token")

    for table_name in (
        "async_presentation_generation_tasks",
        "template_create_infos",
        "imageasset",
        "templates",
        "presentations",
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{table_name}_team_id"))
            batch_op.drop_index(batch_op.f(f"ix_{table_name}_owner_user_id"))
            batch_op.drop_column("visibility")
            batch_op.drop_column("team_id")
            batch_op.drop_column("owner_user_id")

    op.drop_index(op.f("ix_themes_share_token"), table_name="themes")
    op.drop_index(op.f("ix_themes_team_id"), table_name="themes")
    op.drop_index(op.f("ix_themes_owner_user_id"), table_name="themes")
    op.drop_table("themes")

    op.drop_index(
        op.f("ix_integration_clients_default_team_id"), table_name="integration_clients"
    )
    op.drop_index(
        op.f("ix_integration_clients_client_id"), table_name="integration_clients"
    )
    op.drop_table("integration_clients")

    op.drop_index(
        op.f("ix_team_memberships_team_id"), table_name="team_memberships"
    )
    op.drop_index(
        op.f("ix_team_memberships_user_id"), table_name="team_memberships"
    )
    op.drop_table("team_memberships")

    op.drop_index(op.f("ix_teams_slug"), table_name="teams")
    op.drop_table("teams")

    op.drop_index(op.f("ix_users_external_user_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
