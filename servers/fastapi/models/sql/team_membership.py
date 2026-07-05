from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class TeamMembershipModel(SQLModel, table=True):
    __tablename__ = "team_memberships"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    )
    team_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    )
    role: str = Field(default="member", sa_column=Column(String, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        )
    )
