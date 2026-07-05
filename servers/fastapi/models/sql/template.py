from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class TemplateModel(SQLModel, table=True):
    __tablename__ = "templates"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        description="UUID for the template (matches presentation_id)",
    )
    name: str = Field(description="Human friendly template name")
    description: Optional[str] = Field(
        default=None, description="Optional template description"
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        ),
    )
    owner_user_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("users.id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
    )
    team_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("teams.id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
    )
    visibility: str = Field(default="private", sa_column=Column(String, nullable=False))
    share_token: Optional[str] = Field(
        default=None,
        sa_column=Column(String, unique=True, index=True, nullable=True),
    )
