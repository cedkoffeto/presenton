from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from services.database import async_session_maker
from utils.access_control import (
    bootstrap_default_identity,
    resolve_actor_for_username,
    resolve_integration_actor,
)
from utils.get_env import get_can_change_keys_env, is_disable_auth_enabled
from utils.simple_auth import (
    get_configured_auth_username,
    get_auth_status,
    get_basic_auth_credentials_from_request,
    get_session_token_from_request,
    verify_credentials,
)
from utils.user_config import update_env_with_user_config


class UserConfigEnvUpdateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if get_can_change_keys_env() != "false":
            update_env_with_user_config()
        return await call_next(request)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    _EXEMPT_PREFIXES = (
        "/api/v1/auth/",
    )
    _PROTECTED_NON_API_PATHS = {
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._EXEMPT_PREFIXES)

    def _requires_auth(self, path: str) -> bool:
        if path.startswith("/api/"):
            return True
        # PPTX export may re-fetch slide images without session/basic headers.
        if path.startswith("/app_data/images/"):
            return False
        if path.startswith("/app_data/"):
            return True
        return path in self._PROTECTED_NON_API_PATHS

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if (
            request.method == "OPTIONS"
            or not self._requires_auth(path)
            or self._is_exempt(path)
            or (
                request.method == "GET"
                and path.startswith("/api/v1/ppt/presentation/")
                and bool(request.query_params.get("share_token"))
            )
            or path.startswith("/api/v1/ppt/themes/share/")
        ):
            return await call_next(request)

        if is_disable_auth_enabled():
            async with async_session_maker() as session:
                actor = await bootstrap_default_identity(session, "electron")
            request.state.auth_username = actor.user.username
            request.state.auth_actor = actor
            return await call_next(request)

        try:
            async with async_session_maker() as session:
                integration_actor = await resolve_integration_actor(request, session)
                if integration_actor:
                    request.state.auth_username = integration_actor.user.username
                    request.state.auth_actor = integration_actor
                    return await call_next(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )

        auth_status = get_auth_status(get_session_token_from_request(request))
        if not auth_status["configured"]:
            return JSONResponse(
                status_code=428,
                content={
                    "detail": "Login setup is required",
                    "setup_required": True,
                },
            )

        if not auth_status["authenticated"]:
            basic_credentials = get_basic_auth_credentials_from_request(request)
            if basic_credentials and verify_credentials(
                basic_credentials[0], basic_credentials[1]
            ):
                username = basic_credentials[0].strip()
                async with async_session_maker() as session:
                    actor = await resolve_actor_for_username(
                        session,
                        username,
                        allow_bootstrap_admin=username == (get_configured_auth_username() or "").strip(),
                    )
                request.state.auth_username = username
                request.state.auth_actor = actor
                return await call_next(request)

            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )

        username = auth_status.get("username")
        async with async_session_maker() as session:
            actor = await resolve_actor_for_username(
                session,
                username,
                allow_bootstrap_admin=username == (get_configured_auth_username() or "").strip(),
            )
        request.state.auth_username = username
        request.state.auth_actor = actor
        return await call_next(request)
