import secrets
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from starlette.responses import JSONResponse, RedirectResponse

from models.sql.user import UserModel
from services.database import async_session_maker
from utils.access_control import (
    bootstrap_default_identity,
    get_default_team,
    get_or_create_team,
    get_or_create_user,
    resolve_actor_for_username,
)
from utils.get_env import (
    get_default_team_name_env,
    get_oidc_client_id_env,
    get_oidc_client_secret_env,
    get_oidc_issuer_url_env,
    get_oidc_redirect_uri_env,
    get_oidc_scopes_env,
    is_oidc_enabled,
)
from utils.simple_auth import (
    clear_session_cookie,
    create_session_token,
    get_auth_status,
    get_basic_auth_credentials_from_request,
    get_session_token_from_request,
    is_auth_configured,
    set_session_cookie,
    setup_initial_credentials,
    verify_credentials,
)
from utils.get_env import is_disable_auth_enabled

API_V1_AUTH_ROUTER = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


class AuthCredentialsRequest(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=6, max_length=256)


async def _oidc_discovery_document() -> dict:
    issuer = (get_oidc_issuer_url_env() or "").strip().rstrip("/")
    if not issuer:
        raise HTTPException(status_code=400, detail="OIDC is not configured")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    async with aiohttp.ClientSession() as session:
        async with session.get(discovery_url) as response:
            if response.status >= 400:
                raise HTTPException(status_code=502, detail="Failed to fetch OIDC discovery document")
            return await response.json()


@API_V1_AUTH_ROUTER.get("/status")
async def get_status(request: Request):
    if is_disable_auth_enabled():
        return {
            "configured": True,
            "authenticated": True,
            "username": "electron",
            "oidc_enabled": is_oidc_enabled(),
            "is_admin": True,
        }
    token = get_session_token_from_request(request)
    status = get_auth_status(token)
    status["oidc_enabled"] = is_oidc_enabled()
    if status["oidc_enabled"] and not status["configured"]:
        status["configured"] = True
    status["is_admin"] = False
    if status.get("authenticated") and status.get("username"):
        try:
            async with async_session_maker() as sql_session:
                actor = await resolve_actor_for_username(
                    sql_session,
                    status["username"],
                    allow_bootstrap_admin=False,
                )
                status["is_admin"] = actor.user.is_admin
        except Exception:
            status["is_admin"] = False
    return status


@API_V1_AUTH_ROUTER.get("/verify")
async def verify_session(request: Request):
    if is_disable_auth_enabled():
        return {"authenticated": True, "username": "electron", "is_admin": True}

    auth_status = get_auth_status(get_session_token_from_request(request))
    if not auth_status["configured"]:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not auth_status["authenticated"]:
        basic_credentials = get_basic_auth_credentials_from_request(request)
        if basic_credentials and verify_credentials(
            basic_credentials[0], basic_credentials[1]
        ):
            return {
                "authenticated": True,
                "username": basic_credentials[0].strip(),
                "is_admin": True,
            }
        raise HTTPException(status_code=401, detail="Unauthorized")

    is_admin = False
    username = auth_status.get("username")
    if username:
        try:
            async with async_session_maker() as sql_session:
                actor = await resolve_actor_for_username(
                    sql_session,
                    username,
                    allow_bootstrap_admin=False,
                )
                is_admin = actor.user.is_admin
        except Exception:
            is_admin = False

    return {
        "authenticated": True,
        "username": username,
        "is_admin": is_admin,
    }


@API_V1_AUTH_ROUTER.post("/setup")
async def setup_credentials(body: AuthCredentialsRequest, request: Request):
    if is_auth_configured():
        raise HTTPException(status_code=409, detail="Credentials already configured")

    try:
        setup_initial_credentials(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    username = body.username.strip()
    try:
        async with async_session_maker() as sql_session:
            await bootstrap_default_identity(sql_session, username)
    except Exception:
        pass
    return JSONResponse(
        {
            "configured": True,
            "authenticated": False,
            "username": username,
        }
    )


@API_V1_AUTH_ROUTER.post("/login")
async def login(body: AuthCredentialsRequest, request: Request):
    if not is_auth_configured():
        raise HTTPException(status_code=428, detail="Login setup is required")

    if not verify_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Unauthorized")

    username = body.username.strip()
    try:
        async with async_session_maker() as sql_session:
            await bootstrap_default_identity(sql_session, username)
    except Exception:
        pass
    token = create_session_token(username)
    response = JSONResponse(
        {
            "configured": True,
            "authenticated": True,
            "username": username,
            "access_token": token,
            "token_type": "bearer",
        }
    )
    set_session_cookie(response, token, request)
    return response


@API_V1_AUTH_ROUTER.post("/logout")
async def logout(request: Request):
    response = JSONResponse({"success": True})
    clear_session_cookie(response, request)
    return response


@API_V1_AUTH_ROUTER.get("/oidc/start")
async def oidc_start(request: Request):
    if not is_oidc_enabled():
        raise HTTPException(status_code=400, detail="OIDC is not configured")
    discovery = await _oidc_discovery_document()
    state = secrets.token_urlsafe(24)
    params = urlencode(
        {
            "client_id": get_oidc_client_id_env(),
            "redirect_uri": get_oidc_redirect_uri_env(),
            "response_type": "code",
            "scope": (get_oidc_scopes_env() or "openid profile email"),
            "state": state,
        }
    )
    response = RedirectResponse(
        url=f"{discovery['authorization_endpoint']}?{params}"
    )
    response.set_cookie(
        "presenton_oidc_state",
        state,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


@API_V1_AUTH_ROUTER.get("/oidc/callback")
async def oidc_callback(request: Request):
    if not is_oidc_enabled():
        raise HTTPException(status_code=400, detail="OIDC is not configured")
    code = (request.query_params.get("code") or "").strip()
    state = (request.query_params.get("state") or "").strip()
    cookie_state = request.cookies.get("presenton_oidc_state") or ""
    if not code or not state or cookie_state != state:
        raise HTTPException(status_code=400, detail="Invalid OIDC callback state")

    discovery = await _oidc_discovery_document()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": get_oidc_redirect_uri_env(),
                "client_id": get_oidc_client_id_env(),
                "client_secret": get_oidc_client_secret_env() or "",
            },
        ) as token_response:
            if token_response.status >= 400:
                raise HTTPException(status_code=502, detail="OIDC token exchange failed")
            token_payload = await token_response.json()
        userinfo_endpoint = discovery.get("userinfo_endpoint")
        if not userinfo_endpoint:
            raise HTTPException(status_code=502, detail="OIDC userinfo endpoint missing")
        async with session.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
        ) as userinfo_response:
            if userinfo_response.status >= 400:
                raise HTTPException(status_code=502, detail="OIDC userinfo request failed")
            userinfo = await userinfo_response.json()

    username = (
        userinfo.get("preferred_username")
        or userinfo.get("email")
        or userinfo.get("sub")
        or ""
    ).strip()
    if not username:
        raise HTTPException(status_code=400, detail="OIDC user info missing username")

    group_name = None
    groups = userinfo.get("groups")
    if isinstance(groups, list) and groups:
        group_name = str(groups[0]).split("/")[-1]

    async with async_session_maker() as sql_session:
        admin_count = await sql_session.scalar(
            select(func.count()).select_from(UserModel).where(UserModel.is_admin == True)
        )
        team = (
            await get_or_create_team(sql_session, slug=group_name, name=group_name)
            if group_name
            else await get_default_team(sql_session)
        )
        await get_or_create_user(
            sql_session,
            username=username,
            email=(userinfo.get("email") or None),
            display_name=(userinfo.get("name") or username),
            auth_provider="oidc",
            external_user_id=(userinfo.get("sub") or username),
            is_admin=not bool(admin_count),
            team=team,
        )

    token = create_session_token(username)
    response = RedirectResponse(url="/")
    set_session_cookie(response, token, request)
    response.delete_cookie(
        "presenton_oidc_state",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response
