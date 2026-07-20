import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import select

from models.sql.integration_client import IntegrationClientModel
from models.sql.team import TeamModel
from models.sql.team_membership import TeamMembershipModel
from models.sql.user import UserModel
from services.database import async_session_maker
from utils.access_control import (
    generate_integration_api_key,
    hash_client_secret,
)
from utils.request_auth import get_request_actor

API_V1_ADMIN_ROUTER = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: Optional[str] = Field(default=None, min_length=2, max_length=120)


class TeamUpdateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: Optional[str] = Field(default=None, min_length=2, max_length=120)


class TeamMembershipCreateRequest(BaseModel):
    user_id: Optional[uuid.UUID] = None
    username: Optional[str] = Field(default=None, min_length=1, max_length=128)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    role: str = Field(default="member", min_length=3, max_length=32)


class IntegrationClientCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    default_team_id: Optional[uuid.UUID] = None
    auto_provision_users: bool = True


class IntegrationClientUpdateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    default_team_id: Optional[uuid.UUID] = None
    auto_provision_users: bool = True
    is_active: bool = True


def _normalize_team_slug(raw_slug: Optional[str], name: str) -> str:
    source = (raw_slug or name or "").strip().lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in source)
    slug = "-".join(part for part in slug.split("-") if part)
    if not slug:
        raise HTTPException(status_code=400, detail="Team slug is required")
    return slug


def _serialize_user(user: UserModel) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "auth_provider": user.auth_provider,
        "is_admin": user.is_admin,
    }


def _serialize_integration_client(client: IntegrationClientModel, team: Optional[TeamModel]) -> dict:
    return {
        "id": str(client.id),
        "client_id": client.client_id,
        "name": client.name,
        "key_prefix": client.key_prefix,
        "is_active": client.is_active,
        "auto_provision_users": client.auto_provision_users,
        "created_at": client.created_at,
        "last_used_at": client.last_used_at,
        "default_team": (
            {
                "id": str(team.id),
                "name": team.name,
                "slug": team.slug,
            }
            if team
            else None
        ),
    }


async def _require_admin(request: Request):
    actor = get_request_actor(request)
    if not actor.user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return actor


@API_V1_ADMIN_ROUTER.get("/session")
async def get_admin_session(request: Request):
    actor = await _require_admin(request)
    return {
        "user": _serialize_user(actor.user),
        "team": (
            {
                "id": str(actor.team.id),
                "name": actor.team.name,
                "slug": actor.team.slug,
            }
            if actor.team
            else None
        ),
    }


@API_V1_ADMIN_ROUTER.get("/users")
async def list_users(request: Request, query: str = Query(default="", max_length=128)):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        statement = select(UserModel).order_by(UserModel.created_at.desc()).limit(50)
        cleaned_query = query.strip()
        if cleaned_query:
            like_query = f"%{cleaned_query}%"
            statement = (
                select(UserModel)
                .where(
                    or_(
                        UserModel.username.ilike(like_query),
                        UserModel.email.ilike(like_query),
                        UserModel.display_name.ilike(like_query),
                    )
                )
                .order_by(UserModel.created_at.desc())
                .limit(50)
            )
        users = (await sql_session.execute(statement)).scalars().all()
        return [_serialize_user(user) for user in users]


@API_V1_ADMIN_ROUTER.get("/teams")
async def list_teams(request: Request):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        teams = (
            await sql_session.execute(
                select(TeamModel).order_by(func.lower(TeamModel.name).asc())
            )
        ).scalars().all()
        memberships = (
            await sql_session.execute(
                select(TeamMembershipModel, UserModel)
                .join(UserModel, UserModel.id == TeamMembershipModel.user_id)
                .order_by(TeamMembershipModel.created_at.asc())
            )
        ).all()

        members_by_team: dict[uuid.UUID, list[dict]] = {}
        for membership, user in memberships:
            members_by_team.setdefault(membership.team_id, []).append(
                {
                    "membership_id": str(membership.id),
                    "role": membership.role,
                    "user": _serialize_user(user),
                }
            )

        return [
            {
                "id": str(team.id),
                "name": team.name,
                "slug": team.slug,
                "created_at": team.created_at,
                "members": members_by_team.get(team.id, []),
                "member_count": len(members_by_team.get(team.id, [])),
            }
            for team in teams
        ]


@API_V1_ADMIN_ROUTER.post("/teams")
async def create_team(request: Request, body: TeamCreateRequest):
    await _require_admin(request)
    slug = _normalize_team_slug(body.slug, body.name)
    async with async_session_maker() as sql_session:
        existing = await sql_session.scalar(select(TeamModel).where(TeamModel.slug == slug))
        if existing is not None:
            raise HTTPException(status_code=409, detail="A team with this slug already exists")
        team = TeamModel(name=body.name.strip(), slug=slug)
        sql_session.add(team)
        await sql_session.commit()
        await sql_session.refresh(team)
        return {
            "id": str(team.id),
            "name": team.name,
            "slug": team.slug,
            "created_at": team.created_at,
            "members": [],
            "member_count": 0,
        }


@API_V1_ADMIN_ROUTER.patch("/teams/{team_id}")
async def update_team(request: Request, team_id: uuid.UUID, body: TeamUpdateRequest):
    await _require_admin(request)
    slug = _normalize_team_slug(body.slug, body.name)
    async with async_session_maker() as sql_session:
        team = await sql_session.get(TeamModel, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        duplicate = await sql_session.scalar(
            select(TeamModel).where(TeamModel.slug == slug, TeamModel.id != team_id)
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="A team with this slug already exists")
        team.name = body.name.strip()
        team.slug = slug
        sql_session.add(team)
        await sql_session.commit()
        await sql_session.refresh(team)
        return {
            "id": str(team.id),
            "name": team.name,
            "slug": team.slug,
            "created_at": team.created_at,
        }


@API_V1_ADMIN_ROUTER.delete("/teams/{team_id}")
async def delete_team(request: Request, team_id: uuid.UUID):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        team = await sql_session.get(TeamModel, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        membership_count = await sql_session.scalar(
            select(func.count())
            .select_from(TeamMembershipModel)
            .where(TeamMembershipModel.team_id == team_id)
        )
        if membership_count:
            raise HTTPException(
                status_code=409,
                detail="Remove all team members before deleting this team",
            )
        await sql_session.delete(team)
        await sql_session.commit()
        return {"success": True}


@API_V1_ADMIN_ROUTER.post("/teams/{team_id}/members")
async def add_team_member(
    request: Request, team_id: uuid.UUID, body: TeamMembershipCreateRequest
):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        team = await sql_session.get(TeamModel, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")

        user = None
        if body.user_id:
            user = await sql_session.get(UserModel, body.user_id)
        elif body.email:
            user = await sql_session.scalar(
                select(UserModel).where(UserModel.email == body.email.strip())
            )
        elif body.username:
            user = await sql_session.scalar(
                select(UserModel).where(UserModel.username == body.username.strip())
            )

        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        existing = await sql_session.scalar(
            select(TeamMembershipModel).where(
                TeamMembershipModel.team_id == team_id,
                TeamMembershipModel.user_id == user.id,
            )
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="User is already on this team")

        membership = TeamMembershipModel(
            team_id=team_id,
            user_id=user.id,
            role=(body.role or "member").strip() or "member",
        )
        sql_session.add(membership)
        await sql_session.commit()
        await sql_session.refresh(membership)
        return {
            "membership_id": str(membership.id),
            "role": membership.role,
            "user": _serialize_user(user),
        }


@API_V1_ADMIN_ROUTER.delete("/teams/{team_id}/members/{membership_id}")
async def remove_team_member(
    request: Request, team_id: uuid.UUID, membership_id: uuid.UUID
):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        membership = await sql_session.scalar(
            select(TeamMembershipModel).where(
                TeamMembershipModel.id == membership_id,
                TeamMembershipModel.team_id == team_id,
            )
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="Membership not found")
        await sql_session.delete(membership)
        await sql_session.commit()
        return {"success": True}


@API_V1_ADMIN_ROUTER.get("/integrations")
async def list_integrations(request: Request):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        clients = (
            await sql_session.execute(
                select(IntegrationClientModel).order_by(IntegrationClientModel.created_at.desc())
            )
        ).scalars().all()
        team_ids = [client.default_team_id for client in clients if client.default_team_id]
        teams_by_id: dict[uuid.UUID, TeamModel] = {}
        if team_ids:
            teams = (
                await sql_session.execute(
                    select(TeamModel).where(TeamModel.id.in_(team_ids))
                )
            ).scalars().all()
            teams_by_id = {team.id: team for team in teams}
        return [
            _serialize_integration_client(client, teams_by_id.get(client.default_team_id))
            for client in clients
        ]


@API_V1_ADMIN_ROUTER.post("/integrations")
async def create_integration(request: Request, body: IntegrationClientCreateRequest):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        team = None
        if body.default_team_id:
            team = await sql_session.get(TeamModel, body.default_team_id)
            if team is None:
                raise HTTPException(status_code=404, detail="Default team not found")

        client_id, key_prefix, api_key = generate_integration_api_key()
        client = IntegrationClientModel(
            client_id=client_id,
            name=body.name.strip(),
            key_prefix=key_prefix,
            secret_hash=hash_client_secret(api_key.split(".", 1)[1]),
            default_team_id=body.default_team_id,
            auto_provision_users=body.auto_provision_users,
            is_active=True,
        )
        sql_session.add(client)
        await sql_session.commit()
        await sql_session.refresh(client)
        return {
            "client": _serialize_integration_client(client, team),
            "api_key": api_key,
        }


@API_V1_ADMIN_ROUTER.patch("/integrations/{integration_id}")
async def update_integration(
    request: Request,
    integration_id: uuid.UUID,
    body: IntegrationClientUpdateRequest,
):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        client = await sql_session.get(IntegrationClientModel, integration_id)
        if client is None:
            raise HTTPException(status_code=404, detail="Integration not found")

        team = None
        if body.default_team_id:
            team = await sql_session.get(TeamModel, body.default_team_id)
            if team is None:
                raise HTTPException(status_code=404, detail="Default team not found")

        client.name = body.name.strip()
        client.default_team_id = body.default_team_id
        client.auto_provision_users = body.auto_provision_users
        client.is_active = body.is_active
        sql_session.add(client)
        await sql_session.commit()
        await sql_session.refresh(client)
        return _serialize_integration_client(client, team)


@API_V1_ADMIN_ROUTER.post("/integrations/{integration_id}/rotate-key")
async def rotate_integration_key(request: Request, integration_id: uuid.UUID):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        client = await sql_session.get(IntegrationClientModel, integration_id)
        if client is None:
            raise HTTPException(status_code=404, detail="Integration not found")

        client_id, key_prefix, api_key = generate_integration_api_key()
        client.client_id = client_id
        client.key_prefix = key_prefix
        client.secret_hash = hash_client_secret(api_key.split(".", 1)[1])
        sql_session.add(client)
        await sql_session.commit()
        await sql_session.refresh(client)

        team = None
        if client.default_team_id:
            team = await sql_session.get(TeamModel, client.default_team_id)
        return {
            "client": _serialize_integration_client(client, team),
            "api_key": api_key,
        }


@API_V1_ADMIN_ROUTER.delete("/integrations/{integration_id}")
async def delete_integration(request: Request, integration_id: uuid.UUID):
    await _require_admin(request)
    async with async_session_maker() as sql_session:
        client = await sql_session.get(IntegrationClientModel, integration_id)
        if client is None:
            raise HTTPException(status_code=404, detail="Integration not found")
        await sql_session.delete(client)
        await sql_session.commit()
        return {"success": True}
