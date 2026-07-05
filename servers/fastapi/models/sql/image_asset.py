from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class ImageAsset(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        ),
    )
    is_uploaded: bool = Field(default=False)
    path: str
    extras: Optional[dict] = Field(sa_column=Column(JSON), default=None)
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
