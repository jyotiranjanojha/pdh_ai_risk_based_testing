import hashlib
from typing import Callable, Optional, Tuple
import jwt
from fastapi import Depends, Header, HTTPException, status

from app.config import settings
from app.schemas.models import CurrentUser, UserRole


ROLE_HIERARCHY = {
    "AUTHOR": 1,
    "APPROVER": 2,
    "ADMIN": 3,
}


def hash_api_key(raw_key: str) -> str:
    """Compute SHA-256 hex digest of an orchestrator X-API-Key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class AuthLookup:
    """Pluggable lookup hooks populated by the DB client at startup."""

    get_member_role: Optional[Callable[[str, str], Optional[UserRole]]] = None
    get_team_by_api_key_hash: Optional[Callable[[str], Optional[str]]] = None


auth_lookup = AuthLookup()


def _parse_bearer_jwt(authorization: Optional[str]) -> Tuple[str, Optional[UserRole], Optional[str]]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization Bearer token.",
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            settings.OIDC_JWT_SECRET,
            algorithms=[settings.OIDC_JWT_ALGORITHM],
            audience=settings.OIDC_AUDIENCE,
            options={"verify_aud": bool(settings.OIDC_AUDIENCE)},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid OIDC/SSO JWT: {exc}",
        ) from exc

    email = payload.get("email") or payload.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT token missing 'email' or 'sub' claim.",
        )
    role = payload.get("role")
    team_id = payload.get("team_id")
    return email, role, team_id


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_mock_user_email: Optional[str] = Header(default=None, alias="X-Mock-User-Email"),
    x_mock_user_role: Optional[str] = Header(default=None, alias="X-Mock-User-Role"),
    x_team_id: Optional[str] = Header(default=None, alias="X-Team-Id"),
) -> CurrentUser:
    """
    Authenticate UI/interactive requests via:
    - OIDC/SSO Bearer JWT (always supported)
    - X-Mock-User-Email + X-Mock-User-Role headers when SNOWFLAKE_MOCK_MODE=true
    """
    if (settings.SNOWFLAKE_MOCK_MODE or settings.ALLOW_DEMO_AUTH_HEADERS) and not authorization:
        email = x_mock_user_email or "pdh.admin@enterprise.com"
        role_raw = (x_mock_user_role or "ADMIN").upper()
        if role_raw not in ROLE_HIERARCHY:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Invalid role '{role_raw}'. Must be AUTHOR, APPROVER, or ADMIN.",
            )
        role: UserRole = role_raw  # type: ignore[assignment]
        if x_team_id and auth_lookup.get_member_role and not x_mock_user_role:
            db_role = auth_lookup.get_member_role(x_team_id, email)
            if db_role:
                role = db_role
        return CurrentUser(email=email, role=role, team_id=x_team_id, auth_method="MOCK_HEADER")

    email, jwt_role, jwt_team_id = _parse_bearer_jwt(authorization)
    effective_team = x_team_id or jwt_team_id
    resolved_role: Optional[UserRole] = None

    if effective_team and auth_lookup.get_member_role:
        resolved_role = auth_lookup.get_member_role(effective_team, email)

    if not resolved_role and jwt_role and jwt_role.upper() in ROLE_HIERARCHY:
        resolved_role = jwt_role.upper()  # type: ignore[assignment]

    if not resolved_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no assigned RBAC role in RBT_TEAM_MEMBERS.",
        )

    return CurrentUser(email=email, role=resolved_role, team_id=effective_team, auth_method="JWT")


def require_role(min_role: UserRole):
    """FastAPI dependency factory enforcing minimum RBAC role."""

    async def _role_guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if ROLE_HIERARCHY[user.role] < ROLE_HIERARCHY[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role} role or higher; current role is {user.role}.",
            )
        return user

    return _role_guard


async def get_service_or_user(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    x_mock_user_email: Optional[str] = Header(default=None, alias="X-Mock-User-Email"),
    x_mock_user_role: Optional[str] = Header(default=None, alias="X-Mock-User-Role"),
    x_team_id: Optional[str] = Header(default=None, alias="X-Team-Id"),
) -> CurrentUser:
    """
    Service-to-service guard for /run-gate and /runs/{run_id}:
    Accepts either X-API-Key (verified via SHA-256 against RBT_TEAMS) or SSO JWT / Mock headers.
    """
    if x_api_key:
        key_hash = hash_api_key(x_api_key)
        team_id = auth_lookup.get_team_by_api_key_hash(key_hash) if auth_lookup.get_team_by_api_key_hash else None
        if not team_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-API-Key.",
            )
        return CurrentUser(
            email="orchestrator@service.local",
            role="ADMIN",
            team_id=team_id,
            auth_method="API_KEY",
        )

    return await get_current_user(
        authorization=authorization,
        x_mock_user_email=x_mock_user_email,
        x_mock_user_role=x_mock_user_role,
        x_team_id=x_team_id,
    )
