from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class IntegrationClientModel(SQLModel, table=True):
    __tablename__ = "integration_clients"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    client_id: str = Field(
        sa_column=Column(String, unique=True, index=True, nullable=False)
    )
    name: str
    key_prefix: str = Field(sa_column=Column(String, index=True, nullable=False))
    secret_hash: str
    default_team_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("teams.id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
    )
    is_active: bool = Field(sa_column=Column(Boolean), default=True)
    auto_provision_users: bool = Field(sa_column=Column(Boolean), default=True)
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        )
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
