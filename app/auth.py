from fastapi import Header, HTTPException, status

from app.config import settings


async def require_auth(authorization: str = Header(default="")) -> None:
    """Every route depends on this. Expects: Authorization: Bearer <token>"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.middleware_auth_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
