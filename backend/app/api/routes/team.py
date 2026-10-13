"""Team management and gateway API key lifecycle.

RBAC summary:
  - Admin: create/list/deactivate members, resend an invite, issue/list/
    revoke ANY member's key. Each of these emails the affected member. Cannot be deactivated (there is exactly one admin — see
    app/services/bootstrap.py) and cannot be created through /members (that route
    always creates role=member).
  - Member: cannot touch /members/* routes (403 via require_admin). Can
    manage only their OWN key through /me/api-keys/* — issue, list, revoke.

Every change here is written to admin_events in the same transaction
(app/services/admin_audit.py). A raw key or password is never part of it.

Invariant enforced at both issuance paths (admin-issues-for-member and
member-issues-for-self): a user may have at most one active (non-revoked)
gateway API key at a time. Revoke before rotating.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import common_rate_limit, get_current_user, require_admin
from app.core.security import generate_api_key, generate_temp_password, hash_password
from app.db.models import ApiKey, Role, TokenPurpose, User
from app.db.session import get_db
from app.schemas.auth import UserOut
from app.schemas.team import (
    ApiKeyCreatedOut,
    ApiKeyOut,
    CreateApiKeyRequest,
    CreateMemberRequest,
    MemberCreatedOut,
)
from app.services.admin_audit import record
from app.services.auth_tokens import issue_auth_token
from app.services.mailer import invite_email, notice_email, send_email

router = APIRouter(
    prefix="/team",
    tags=["team"],
    dependencies=[Depends(get_current_user), Depends(common_rate_limit)],
)


async def _assert_no_active_key(db: AsyncSession, user_id: uuid.UUID) -> None:
    existing = await db.execute(select(ApiKey.id).where(ApiKey.user_id == user_id, ApiKey.revoked.is_(False)))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already has an active API key — revoke it first")


# ---- admin: member management ----------------------------------------------


@router.post("/members", response_model=MemberCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_member(
    body: CreateMemberRequest,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberCreatedOut:
    temp_password = generate_temp_password()
    member = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(temp_password),
        role=Role.member,
    )
    db.add(member)
    record(db, request, admin, "member.create", member.email)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc
    await db.refresh(member)

    token = await issue_auth_token(db, member.id, TokenPurpose.invite)
    background.add_task(send_email, member.email, *invite_email(member.name, token))

    return MemberCreatedOut(user=UserOut.model_validate(member), temporary_password=temp_password)


@router.post("/members/{member_id}/resend-invite", status_code=status.HTTP_204_NO_CONTENT)
async def resend_invite(
    member_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Emails a fresh set-password link; the previous invite link stops working."""
    member = await db.get(User, member_id)
    if not member or not member.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")

    record(db, request, admin, "member.invite_resend", member.email)
    token = await issue_auth_token(db, member.id, TokenPurpose.invite)  # commits, and the record with it
    background.add_task(send_email, member.email, *invite_email(member.name, token))


@router.get("/members", response_model=list[UserOut])
async def list_members(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars().all())


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_member(
    member_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    member = await db.get(User, member_id)
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if member.role == Role.admin:
        raise HTTPException(status.HTTP_409_CONFLICT, "The admin account cannot be deactivated")
    was_active = member.is_active
    member.is_active = False
    if was_active:
        record(db, request, admin, "member.deactivate", member.email)
    await db.commit()

    if was_active:
        background.add_task(
            send_email,
            member.email,
            *notice_email(member.name, "Your Urumi account was deactivated", "An admin deactivated your Urumi account. You can no longer log in."),
        )


# ---- admin: issue/list/revoke a SPECIFIC member's key -----------------------


@router.post(
    "/members/{member_id}/api-keys",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def issue_api_key(
    member_id: uuid.UUID,
    body: CreateApiKeyRequest,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreatedOut:
    member = await db.get(User, member_id)
    if not member or not member.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    await _assert_no_active_key(db, member.id)

    raw_key, key_hash, prefix = generate_api_key()
    key = ApiKey(user_id=member.id, key_hash=key_hash, prefix=prefix, name=body.name)
    db.add(key)
    record(db, request, admin, "api_key.issue", member.email, prefix=prefix)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "User already has an active API key — revoke it first") from exc
    await db.refresh(key)

    # The key itself is never emailed — the admin hands it over from the response.
    background.add_task(
        send_email,
        member.email,
        *notice_email(member.name, "A Urumi API key was issued for you", f"An admin issued a gateway API key ({prefix}…) for your account."),
    )

    return ApiKeyCreatedOut(id=key.id, raw_key=raw_key, prefix=prefix, name=key.name, created_at=key.created_at)


@router.get("/members/{member_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    member_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    result = await db.execute(select(ApiKey).where(ApiKey.user_id == member_id).order_by(ApiKey.created_at))
    return list(result.scalars().all())


@router.post("/members/{member_id}/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_api_key(
    member_id: uuid.UUID,
    key_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if not key or key.user_id != member_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")

    member = await db.get(User, member_id)
    was_active = not key.revoked
    key.revoked = True
    key.revoked_at = datetime.now(timezone.utc)
    if was_active:
        record(db, request, admin, "api_key.revoke", member.email if member else str(member_id), prefix=key.prefix)
    await db.commit()
    await db.refresh(key)

    if was_active and member:
        background.add_task(
            send_email,
            member.email,
            *notice_email(member.name, "Your Urumi API key was revoked", f"An admin revoked your gateway API key ({key.prefix}…). It no longer works."),
        )
    return key


# ---- member: self-service on OWN key only (no user_id in the path — the ----
# ---- caller's identity comes from the JWT, so a member can never target ----
# ---- anyone else's key) -----------------------------------------------------


@router.post("/me/api-keys", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def issue_my_api_key(
    body: CreateApiKeyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreatedOut:
    await _assert_no_active_key(db, user.id)

    raw_key, key_hash, prefix = generate_api_key()
    key = ApiKey(user_id=user.id, key_hash=key_hash, prefix=prefix, name=body.name)
    db.add(key)
    record(db, request, user, "api_key.issue", user.email, prefix=prefix)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "User already has an active API key — revoke it first") from exc
    await db.refresh(key)

    return ApiKeyCreatedOut(id=key.id, raw_key=raw_key, prefix=prefix, name=key.name, created_at=key.created_at)


@router.get("/me/api-keys", response_model=list[ApiKeyOut])
async def list_my_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at))
    return list(result.scalars().all())


@router.post("/me/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_my_api_key(
    key_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if not key or key.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")

    if not key.revoked:
        record(db, request, user, "api_key.revoke", user.email, prefix=key.prefix)
    key.revoked = True
    key.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(key)
    return key
