from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class UserModel(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    username: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    email: Optional[str] = Field(
        default=None,
        sa_column=Column(String, unique=True, index=True, nullable=True),
    )
    display_name: Optional[str] = None
    auth_provider: str = Field(default="local")
    external_user_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, index=True, nullable=True),
    )
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        )
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            onupdate=get_current_utc_datetime,
        )
    )
