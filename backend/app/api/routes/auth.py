import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    SESSION_COOKIE,
    get_current_user,
    get_optional_user,
    get_redis,
    ip_rate_limit,
    revoked_session_key,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import AuthToken, TokenPurpose, User
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.auth import (
    AdminSignupRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    SessionOut,
    SetPasswordRequest,
    TokenResponse,
    UserOut,
)
from app.services.auth_tokens import consume_auth_token, issue_auth_token
from app.services.bootstrap import create_admin_if_absent, create_first_store_if_absent
from app.services.conversations import end_chat
from app.services.mailer import notice_email, reset_email, send_email

router = APIRouter(prefix="/auth", tags=["auth"])

# IP-based, strict: brute-force protection on the pre-auth endpoints, and on
# change-password (which checks a password too). One limiter serves them all
# because its buckets are keyed per path.
_ip_rate_limit = ip_rate_limit(capacity=5, refill_per_minute=5)

_DUMMY_HASH = hash_password("no account has this password")  # see login()


def _cookie_flags() -> dict:
    """How the session cookie is set and, with the same flags, removed."""
    return {
        "httponly": True,  # page scripts cannot read it, so a script that should not be there cannot steal the session
        "samesite": "strict",  # requests made from other sites do not carry it
        "secure": settings.app_base_url.startswith("https://"),  # over https, never sent in the clear
    }


def _start_session(response: Response, user: User) -> TokenResponse:
    """Signs the user in. A browser keeps the token as the cookie; scripts and tests take it from the body."""
    token = create_access_token(user.id, user.role.value)
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.jwt_expire_minutes * 60, **_cookie_flags())
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


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
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # bcrypt runs even when the email is unknown, so a wrong email takes as
    # long as a wrong password and reply time doesn't reveal which exist.
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not user.is_active or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    return _start_session(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    """Signs out for real: the browser forgets the cookie, the token in it stops
    working at once, and so do the chat tokens minted for this user."""
    response.delete_cookie(SESSION_COOKIE, **_cookie_flags())
    if user is None:
        return
    token, expires_at = request.state.session
    # Remembered only as long as the token could still have been used.
    await redis.set(revoked_session_key(token), 1, ex=max(1, int(expires_at - time.time())))
    await end_chat(db, redis, user)


@router.get("/session", response_model=SessionOut)
async def session(user: User | None = Depends(get_optional_user)) -> SessionOut:
    """Who is signed in, if anyone. Unlike /auth/me this answers 200 either way,
    so a page can ask on every load without causing an error when nobody is."""
    return SessionOut(user=UserOut.model_validate(user) if user else None)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post(
    "/admin/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_ip_rate_limit)],
)
async def admin_signup(body: AdminSignupRequest, request: Request, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Creates the one and only admin. Only works while no admin exists yet —
    see app/services/bootstrap.py. Once an admin exists (via this route or the
    ADMIN_EMAIL/ADMIN_PASSWORD env vars), this always returns 409.
    """
    admin = await create_admin_if_absent(db, body.email, body.name, body.password)
    if admin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An admin already exists")
    # Read now: if another replica registers the store first, the rollback leaves this row unreadable.
    created = UserOut.model_validate(admin)
    await create_first_store_if_absent(db, admin, request)  # startup could not: the store needs its creator
    return created


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
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Redeems an emailed invite or reset token and logs the user in."""
    user_id = await consume_auth_token(db, body.token)
    user = await db.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired link")

    await _set_password(db, user, body.new_password, background)
    return _start_session(response, user)
