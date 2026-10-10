from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, ip_rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import AuthToken, TokenPurpose, User
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.auth import (
    AdminSignupRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    SetPasswordRequest,
    TokenResponse,
    UserOut,
)
from app.services.auth_tokens import consume_auth_token, issue_auth_token
from app.services.bootstrap import create_admin_if_absent
from app.services.mailer import notice_email, reset_email, send_email

router = APIRouter(prefix="/auth", tags=["auth"])

# IP-based, strict: brute-force protection on the pre-auth endpoints, and on
# change-password (which checks a password too). One limiter serves them all
# because its buckets are keyed per path.
_ip_rate_limit = ip_rate_limit(capacity=5, refill_per_minute=5)

_DUMMY_HASH = hash_password("no account has this password")  # see login()


async def _set_password(db: AsyncSession, user: User, new_password: str, background: BackgroundTasks) -> None:
    """Every password change goes through here: it also kills any outstanding
    invite/reset links for the user and emails them a security notice.
    """
    user.password_hash = hash_password(new_password)
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
    await db.commit()
    background.add_task(
        send_email,
        user.email,
        *notice_email(user.name, "Your Urumi password was changed", "The password for your Urumi account was just changed."),
    )


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_ip_rate_limit)])
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # bcrypt runs even when the email is unknown, so a wrong email takes as
    # long as a wrong password and reply time doesn't reveal which exist.
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not user.is_active or not password_ok:
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
    dependencies=[Depends(_ip_rate_limit)],
)
async def admin_signup(body: AdminSignupRequest, db: AsyncSession = Depends(get_db)) -> User:
    """Creates the one and only admin. Only works while no admin exists yet —
    see app/services/bootstrap.py. Once an admin exists (via this route or the
    ADMIN_EMAIL/ADMIN_PASSWORD env vars), this always returns 409.
    """
    admin = await create_admin_if_absent(db, body.email, body.name, body.password)
    if admin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An admin already exists")
    return admin


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_ip_rate_limit)],
)
async def change_password(
    body: ChangePasswordRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    await _set_password(db, user, body.new_password, background)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_ip_rate_limit)],
)
async def forgot_password(body: ForgotPasswordRequest, background: BackgroundTasks) -> dict[str, str]:
    """Same response, in the same time, whether or not the email is
    registered, so this can't be used to discover which addresses have
    accounts. All the work happens after the response has gone out.
    """
    background.add_task(_send_reset_link, body.email)
    return {"detail": "If that email is registered, a reset link has been sent"}


async def _send_reset_link(email: str) -> None:
    # Own session: the request's one is closed by the time this runs.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user and user.is_active:
            token = await issue_auth_token(db, user.id, TokenPurpose.reset)
            await send_email(user.email, *reset_email(user.name, token))


@router.post("/set-password", response_model=TokenResponse, dependencies=[Depends(_ip_rate_limit)])
async def set_password(
    body: SetPasswordRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Redeems an emailed invite or reset token and logs the user in."""
    user_id = await consume_auth_token(db, body.token)
    user = await db.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired link")

    await _set_password(db, user, body.new_password, background)
    token = create_access_token(user.id, user.role.value)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))
