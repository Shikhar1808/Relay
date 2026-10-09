from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap import create_admin_if_absent
from app.database import get_db
from app.deps import get_current_user, ip_rate_limit
from app.models import User
from app.schemas import AdminSignupRequest, LoginRequest, TokenResponse, UserOut
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# IP-based, strict: brute-force protection on the two pre-auth endpoints.
_login_rate_limit = ip_rate_limit(capacity=5, refill_per_minute=5)
_signup_rate_limit = ip_rate_limit(capacity=5, refill_per_minute=5)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_login_rate_limit)])
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    token = create_access_token(user.id, user.role.value)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post(
    "/admin/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_signup_rate_limit)],
)
async def admin_signup(body: AdminSignupRequest, db: AsyncSession = Depends(get_db)) -> User:
    """Creates the one and only admin. Only works while no admin exists yet —
    see app/bootstrap.py. Once an admin exists (via this route or the
    ADMIN_EMAIL/ADMIN_PASSWORD env vars), this always returns 409.
    """
    admin = await create_admin_if_absent(db, body.email, body.name, body.password)
    if admin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An admin already exists")
    return admin
