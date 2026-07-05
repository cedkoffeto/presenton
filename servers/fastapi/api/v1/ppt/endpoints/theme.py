import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import or_, select

from models.sql.image_asset import ImageAsset
from models.sql.theme import ThemeModel
from services.database import get_async_session
from utils.access_control import (
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_TEAM,
    apply_actor_ownership,
    generate_share_token,
    require_resource_access,
)
from utils.asset_directory_utils import normalize_slide_asset_url
from utils.request_auth import get_request_actor

THEMES_ROUTER = APIRouter(prefix="/themes", tags=["Themes"])


class ThemeRequest(BaseModel):
    name: str
    description: str
    company_name: Optional[str] = None
    logo: Optional[str] = None
    logo_url: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)
    visibility: str = VISIBILITY_PRIVATE


class ThemeUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    company_name: Optional[str] = None
    logo: Optional[str] = None
    logo_url: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    visibility: Optional[str] = None
    public_share_enabled: Optional[bool] = None


class ThemeResponse(BaseModel):
    id: str
    name: str
    description: str
    user: str
    logo: Optional[str] = None
    logo_url: Optional[str] = None
    company_name: Optional[str] = None
    data: dict[str, Any]
    visibility: str
    team_id: Optional[str] = None
    public_share_url: Optional[str] = None


def _public_share_url(request: Request, theme: ThemeModel) -> Optional[str]:
    if not theme.share_token or theme.visibility != VISIBILITY_PUBLIC:
        return None
    return str(request.url_for("get_theme_by_share_token", share_token=theme.share_token))


def _normalize_theme(theme: ThemeModel, request: Request, username: str) -> ThemeResponse:
    return ThemeResponse(
        id=str(theme.id),
        name=theme.name,
        description=theme.description,
        user=username,
        logo=theme.logo,
        logo_url=normalize_slide_asset_url(theme.logo_url) if theme.logo_url else None,
        company_name=theme.company_name,
        data=theme.data or {},
        visibility=theme.visibility,
        team_id=str(theme.team_id) if theme.team_id else None,
        public_share_url=_public_share_url(request, theme),
    )


async def _resolve_logo_url(
    sql_session: AsyncSession, actor, logo: Optional[str]
) -> Optional[str]:
    if not logo:
        return None
    try:
        logo_uuid = uuid.UUID(str(logo))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid logo id") from exc

    image_asset = await sql_session.get(ImageAsset, logo_uuid)
    if not image_asset:
        raise HTTPException(status_code=404, detail="Logo not found")
    require_resource_access(
        actor,
        owner_user_id=image_asset.owner_user_id,
        team_id=image_asset.team_id,
        visibility=image_asset.visibility,
    )
    return normalize_slide_asset_url(image_asset.path)


@THEMES_ROUTER.get("/default", response_model=List[dict[str, Any]])
async def get_default_themes():
    return []


@THEMES_ROUTER.get("/all", response_model=List[ThemeResponse])
async def get_themes(
    request: Request, sql_session: AsyncSession = Depends(get_async_session)
):
    actor = get_request_actor(request)
    result = await sql_session.scalars(
        select(ThemeModel)
        .where(
            or_(
                ThemeModel.owner_user_id == actor.owner_user_id,
                ThemeModel.visibility == VISIBILITY_PUBLIC,
                (
                    (ThemeModel.visibility == VISIBILITY_TEAM)
                    & (ThemeModel.team_id == actor.team_id)
                ),
            )
        )
        .order_by(ThemeModel.created_at.desc())
    )
    return [_normalize_theme(theme, request, actor.user.username) for theme in result]


@THEMES_ROUTER.get("/share/{share_token}", response_model=ThemeResponse, name="get_theme_by_share_token")
async def get_theme_by_share_token(
    share_token: str,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    theme = await sql_session.scalar(
        select(ThemeModel).where(
            ThemeModel.share_token == share_token,
            ThemeModel.visibility == VISIBILITY_PUBLIC,
        )
    )
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    return _normalize_theme(theme, request, "public")


@THEMES_ROUTER.post("/create", response_model=ThemeResponse)
async def create_theme(
    request: Request,
    payload: ThemeRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    logo_url = payload.logo_url or await _resolve_logo_url(sql_session, actor, payload.logo)
    theme = ThemeModel(
        name=payload.name,
        description=payload.description,
        company_name=payload.company_name,
        logo=payload.logo,
        logo_url=logo_url,
        data=payload.data,
        share_token=generate_share_token() if payload.visibility == VISIBILITY_PUBLIC else None,
    )
    apply_actor_ownership(theme, actor, payload.visibility)
    sql_session.add(theme)
    await sql_session.commit()
    await sql_session.refresh(theme)
    return _normalize_theme(theme, request, actor.user.username)


@THEMES_ROUTER.patch("/update/{theme_id}", response_model=ThemeResponse)
async def update_theme(
    theme_id: str,
    request: Request,
    payload: ThemeUpdateRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    theme = await sql_session.get(ThemeModel, uuid.UUID(theme_id))
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    require_resource_access(
        actor,
        owner_user_id=theme.owner_user_id,
        team_id=theme.team_id,
        visibility=theme.visibility,
    )
    if payload.name is not None:
        theme.name = payload.name
    if payload.description is not None:
        theme.description = payload.description
    if payload.company_name is not None:
        theme.company_name = payload.company_name
    if payload.data is not None:
        theme.data = payload.data
    if payload.logo is not None:
        theme.logo = payload.logo
        theme.logo_url = await _resolve_logo_url(sql_session, actor, payload.logo)
    elif payload.logo_url is not None:
        theme.logo_url = payload.logo_url
    if payload.visibility is not None:
        theme.visibility = payload.visibility
    if payload.public_share_enabled is True and not theme.share_token:
        theme.share_token = generate_share_token()
        theme.visibility = VISIBILITY_PUBLIC
    elif payload.public_share_enabled is False:
        theme.share_token = None
        if theme.visibility == VISIBILITY_PUBLIC:
            theme.visibility = VISIBILITY_PRIVATE
    sql_session.add(theme)
    await sql_session.commit()
    await sql_session.refresh(theme)
    return _normalize_theme(theme, request, actor.user.username)


@THEMES_ROUTER.delete("/delete/{theme_id}", status_code=204)
async def delete_theme(
    theme_id: str,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    theme = await sql_session.get(ThemeModel, uuid.UUID(theme_id))
    if not theme:
        return
    require_resource_access(
        actor,
        owner_user_id=theme.owner_user_id,
        team_id=theme.team_id,
        visibility=theme.visibility,
    )
    await sql_session.delete(theme)
    await sql_session.commit()
