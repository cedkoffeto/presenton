from typing import Any, List, Optional
from datetime import datetime
import uuid

from pydantic import BaseModel

from models.sql.slide import SlideModel


class PresentationWithSlides(BaseModel):
    id: uuid.UUID
    content: str
    n_slides: int
    language: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    tone: Optional[str] = None
    verbosity: Optional[str] = None
    slides: List[SlideModel]
    theme: Optional[dict] = None
    fonts: Optional[Any] = None
    visibility: str = "private"
    team_id: Optional[uuid.UUID] = None
    can_edit_access: bool = False
    public_share_url: Optional[str] = None
