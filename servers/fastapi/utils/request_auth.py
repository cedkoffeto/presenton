from fastapi import HTTPException, Request

from utils.access_control import AuthActor


def get_request_actor(request: Request) -> AuthActor:
    actor = getattr(request.state, "auth_actor", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return actor
