from datetime import datetime
import secrets
from typing import Optional
import uuid

from sqlalchemy import JSON, Column, ForeignKey, String
from sqlmodel import Field, SQLModel


class AsyncPresentationGenerationTaskModel(SQLModel, table=True):

    __tablename__ = "async_presentation_generation_tasks"

    id: str = Field(
        default_factory=lambda: f"task-{secrets.token_hex(32)}", primary_key=True
    )
    status: str
    message: Optional[str] = None
    error: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    data: Optional[dict] = Field(sa_column=Column(JSON), default=None)
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
