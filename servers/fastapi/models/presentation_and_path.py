from pydantic import BaseModel
import uuid


class PresentationAndPath(BaseModel):
    presentation_id: uuid.UUID
    path: str


class PresentationPathAndEditPath(PresentationAndPath):
    edit_path: str
    download_url: str | None = None
    public_share_url: str | None = None
