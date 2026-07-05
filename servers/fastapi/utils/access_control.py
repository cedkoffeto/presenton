import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.integration_client import IntegrationClientModel
from models.sql.team import TeamModel
from models.sql.team_membership import TeamMembershipModel
from models.sql.user import UserModel
from utils.get_env import (
    get_default_team_name_env,
    get_default_team_slug_env,
)
from utils.datetime_utils import get_current_utc_datetime

VISIBILITY_PRIVATE = "private"
VISIBILITY_TEAM = "team"
VISIBILITY_PUBLIC = "public"
ALLOWED_VISIBILITIES = {VISIBILITY_PRIVATE, VISIBILITY_TEAM, VISIBILITY_PUBLIC}
INTEGRATION_API_KEY_PREFIX = "pct"


@dataclass
class AuthActor:
    user: UserModel
    team: Optional[TeamModel]
    integration_client: Optional[IntegrationClientModel] = None

    @property
    def owner_user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def team_id(self) -> Optional[uuid.UUID]:
        return self.team.id if self.team else None


def slugify_team_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned or "default-team"


def hash_client_secret(secret: str) -> str:
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


def verify_client_secret(secret: str, hashed_secret: str) -> bool:
    candidate = hash_client_secret(secret)
    return hmac.compare_digest(candidate, hashed_secret)


def generate_share_token() -> str:
    return secrets.token_urlsafe(32)


def generate_integration_client_id() -> str:
    return f"client_{secrets.token_urlsafe(12)}"


def generate_integration_api_key() -> tuple[str, str, str]:
    client_id = generate_integration_client_id()
    raw_secret = secrets.token_urlsafe(32)
    key_prefix = raw_secret[:12]
    api_key = f"{INTEGRATION_API_KEY_PREFIX}_{client_id}.{raw_secret}"
    return client_id, key_prefix, api_key


def parse_integration_api_key(api_key: str) -> tuple[str, str]:
    value = (api_key or "").strip()
    prefix = f"{INTEGRATION_API_KEY_PREFIX}_"
    if not value.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid integration API key")

    payload = value[len(prefix):]
    if "." not in payload:
        raise HTTPException(status_code=401, detail="Invalid integration API key")

    client_id, raw_secret = payload.split(".", 1)
    if not client_id or not raw_secret:
        raise HTTPException(status_code=401, detail="Invalid integration API key")
    return client_id, raw_secret


async def get_team_by_id(
    sql_session: AsyncSession, team_id: Optional[uuid.UUID]
) -> Optional[TeamModel]:
    if not team_id:
        return None
    return await sql_session.get(TeamModel, team_id)


async def get_default_team(sql_session: AsyncSession) -> TeamModel:
    slug = get_default_team_slug_env() or slugify_team_name(
        get_default_team_name_env() or "Default Team"
    )
    name = get_default_team_name_env() or "Default Team"
    return await get_or_create_team(sql_session, slug=slug, name=name)


async def get_or_create_team(
    sql_session: AsyncSession, slug: str, name: Optional[str] = None
) -> TeamModel:
    cleaned_slug = slugify_team_name(slug)
    team = await sql_session.scalar(select(TeamModel).where(TeamModel.slug == cleaned_slug))
    if team:
        return team
    team = TeamModel(slug=cleaned_slug, name=(name or cleaned_slug).strip() or cleaned_slug)
    sql_session.add(team)
    await sql_session.commit()
    await sql_session.refresh(team)
    return team


async def ensure_team_membership(
    sql_session: AsyncSession, user_id: uuid.UUID, team_id: uuid.UUID, role: str = "member"
) -> TeamMembershipModel:
    membership = await sql_session.scalar(
        select(TeamMembershipModel).where(
            TeamMembershipModel.user_id == user_id,
            TeamMembershipModel.team_id == team_id,
        )
    )
    if membership:
        return membership
    membership = TeamMembershipModel(user_id=user_id, team_id=team_id, role=role)
    sql_session.add(membership)
    await sql_session.commit()
    await sql_session.refresh(membership)
    return membership


async def get_user_primary_team(
    sql_session: AsyncSession, user_id: uuid.UUID
) -> Optional[TeamModel]:
    membership = await sql_session.scalar(
        select(TeamMembershipModel)
        .where(TeamMembershipModel.user_id == user_id)
        .order_by(TeamMembershipModel.created_at.asc())
    )
    if not membership:
        return None
    return await get_team_by_id(sql_session, membership.team_id)


async def get_or_create_user(
    sql_session: AsyncSession,
    *,
    username: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    auth_provider: str = "local",
    external_user_id: Optional[str] = None,
    is_admin: bool = False,
    team: Optional[TeamModel] = None,
) -> UserModel:
    existing = await sql_session.scalar(select(UserModel).where(UserModel.username == username))
    if existing:
        changed = False
        if email and existing.email != email:
            existing.email = email
            changed = True
        if display_name and existing.display_name != display_name:
            existing.display_name = display_name
            changed = True
        if external_user_id and existing.external_user_id != external_user_id:
            existing.external_user_id = external_user_id
            changed = True
        if auth_provider and existing.auth_provider != auth_provider:
            existing.auth_provider = auth_provider
            changed = True
        if is_admin and not existing.is_admin:
            existing.is_admin = True
            changed = True
        if changed:
            sql_session.add(existing)
            await sql_session.commit()
            await sql_session.refresh(existing)
        if team:
            await ensure_team_membership(sql_session, existing.id, team.id, "owner" if is_admin else "member")
        return existing

    user = UserModel(
        username=username.strip(),
        email=email,
        display_name=display_name or username.strip(),
        auth_provider=auth_provider,
        external_user_id=external_user_id,
        is_admin=is_admin,
    )
    sql_session.add(user)
    await sql_session.commit()
    await sql_session.refresh(user)
    if team:
        await ensure_team_membership(sql_session, user.id, team.id, "owner" if is_admin else "member")
    return user


async def bootstrap_default_identity(sql_session: AsyncSession, username: str) -> AuthActor:
    team = await get_default_team(sql_session)
    user = await get_or_create_user(
        sql_session,
        username=username,
        display_name=username,
        auth_provider="local",
        is_admin=True,
        team=team,
    )
    return AuthActor(user=user, team=team)


async def resolve_actor_for_username(
    sql_session: AsyncSession,
    username: str,
    *,
    allow_bootstrap_admin: bool = False,
) -> AuthActor:
    cleaned_username = (username or "").strip()
    if not cleaned_username:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = await sql_session.scalar(
        select(UserModel).where(UserModel.username == cleaned_username)
    )
    if user is None:
        if not allow_bootstrap_admin:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return await bootstrap_default_identity(sql_session, cleaned_username)

    team = await get_user_primary_team(sql_session, user.id)
    if team is None and allow_bootstrap_admin:
        team = await get_default_team(sql_session)
        await ensure_team_membership(
            sql_session,
            user.id,
            team.id,
            "owner" if user.is_admin else "member",
        )
    return AuthActor(user=user, team=team)


def _get_integration_api_key_from_request(request: Request) -> Optional[str]:
    header_value = (request.headers.get("X-Presenton-Api-Key") or "").strip()
    if header_value:
        return header_value

    auth_header = (request.headers.get("Authorization") or "").strip()
    bearer_prefix = "Bearer "
    if auth_header.startswith(bearer_prefix):
        candidate = auth_header[len(bearer_prefix):].strip()
        if candidate.startswith(f"{INTEGRATION_API_KEY_PREFIX}_"):
            return candidate
    return None


async def resolve_integration_actor(
    request: Request, sql_session: AsyncSession
) -> Optional[AuthActor]:
    raw_api_key = _get_integration_api_key_from_request(request)
    if not raw_api_key:
        return None

    client_id, client_secret = parse_integration_api_key(raw_api_key)

    integration_client = await sql_session.scalar(
        select(IntegrationClientModel).where(
            IntegrationClientModel.client_id == client_id,
            IntegrationClientModel.is_active == True,
        )
    )
    if (
        not integration_client
        or integration_client.key_prefix != client_secret[:12]
        or not verify_client_secret(client_secret, integration_client.secret_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid integration API key")

    integration_client.last_used_at = get_current_utc_datetime()
    sql_session.add(integration_client)
    await sql_session.commit()
    await sql_session.refresh(integration_client)

    external_user_id = (request.headers.get("X-Presenton-User-Id") or "").strip()
    username = (request.headers.get("X-Presenton-Username") or external_user_id).strip()
    if not username:
        raise HTTPException(status_code=400, detail="Missing X-Presenton-User-Id or X-Presenton-Username")

    email = (request.headers.get("X-Presenton-User-Email") or "").strip() or None
    display_name = (request.headers.get("X-Presenton-User-Name") or username).strip()
    team_slug = (
        request.headers.get("X-Presenton-Team-Slug")
        or request.headers.get("X-Presenton-Team-Name")
        or ""
    ).strip()
    team_name = (request.headers.get("X-Presenton-Team-Name") or team_slug or "").strip()
    team = None
    if team_slug:
        team = await get_or_create_team(sql_session, slug=team_slug, name=team_name or team_slug)
    elif integration_client.default_team_id:
        team = await get_team_by_id(sql_session, integration_client.default_team_id)
    if not team:
        team = await get_default_team(sql_session)

    existing_user = await sql_session.scalar(
        select(UserModel).where(UserModel.username == username)
    )
    if existing_user is None and not integration_client.auto_provision_users:
        raise HTTPException(status_code=403, detail="Integration user provisioning is disabled")

    user = await get_or_create_user(
        sql_session,
        username=username,
        email=email,
        display_name=display_name,
        auth_provider="integration",
        external_user_id=external_user_id or username,
        team=team,
    )
    return AuthActor(user=user, team=team, integration_client=integration_client)


def has_access_to_resource(
    actor: AuthActor,
    *,
    owner_user_id: Optional[uuid.UUID],
    team_id: Optional[uuid.UUID],
    visibility: Optional[str],
) -> bool:
    normalized_visibility = visibility or VISIBILITY_PRIVATE
    if owner_user_id and owner_user_id == actor.user.id:
        return True
    if normalized_visibility == VISIBILITY_PUBLIC:
        return True
    if normalized_visibility == VISIBILITY_TEAM and team_id and actor.team and team_id == actor.team.id:
        return True
    return False


def require_resource_access(
    actor: AuthActor,
    *,
    owner_user_id: Optional[uuid.UUID],
    team_id: Optional[uuid.UUID],
    visibility: Optional[str],
) -> None:
    if not has_access_to_resource(
        actor,
        owner_user_id=owner_user_id,
        team_id=team_id,
        visibility=visibility,
    ):
        raise HTTPException(status_code=404, detail="Resource not found")


def apply_actor_ownership(obj, actor: AuthActor, visibility: Optional[str] = None):
    obj.owner_user_id = actor.owner_user_id
    obj.team_id = actor.team_id
    obj.visibility = visibility or getattr(obj, "visibility", VISIBILITY_PRIVATE)
    return obj
