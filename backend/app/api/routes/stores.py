"""Stores. Creating and deleting one only writes the row: the store worker
(app/worker.py) installs or removes it in the cluster and moves its status along.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import common_rate_limit, get_current_user, require_admin
from app.db.models import Role, Store, StoreMember, StoreStatus, User
from app.db.session import get_db
from app.schemas.auth import UserOut
from app.schemas.stores import AssignMemberRequest, CreateStoreRequest, DeleteStoreRequest, StoreMemberOut, StoreOut
from app.services.admin_audit import record
from app.services.mailer import notice_email, send_email
from app.services.stores import stores_for_user

router = APIRouter(
    prefix="/stores",
    tags=["stores"],
    dependencies=[Depends(get_current_user), Depends(common_rate_limit)],
)


async def _get(db: AsyncSession, store_id: uuid.UUID) -> Store:
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    return store


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(
    body: CreateStoreRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Store:
    # shortcut: count then insert, so two creates at the same moment can pass the cap by one. Fine for one admin.
    if await db.scalar(select(func.count()).select_from(Store)) >= settings.max_stores:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There can be at most {settings.max_stores} stores. Delete one first.")
    store = Store(
        name=body.name,
        slug=body.subdomain,
        status=StoreStatus.pending,
        url=f"{settings.store_scheme}://{body.subdomain}.{settings.store_domain}",
        created_by=admin.id,
    )
    db.add(store)
    record(db, request, admin, "store.create", store.slug, name=body.name)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A store with this subdomain already exists") from exc
    await db.refresh(store)
    return store


@router.post("/{store_id}/retry", response_model=StoreOut)
async def retry_store(
    store_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Store:
    """Hands a failed store back to the worker. Every step it runs is safe to repeat,
    so a store that got halfway is finished, not made twice."""
    store = await _get(db, store_id)
    if store.status != StoreStatus.failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a failed store can be retried")
    store.status, store.error = StoreStatus.pending, None
    record(db, request, admin, "store.retry", store.slug)
    await db.commit()
    await db.refresh(store)
    return store


@router.delete("/{store_id}", response_model=StoreOut, status_code=status.HTTP_202_ACCEPTED)
async def delete_store(
    store_id: uuid.UUID,
    body: DeleteStoreRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Store:
    """The worker then removes its MCP server, its namespace (with its volumes and
    Secrets) and finally the row. A failed delete leaves a failed store to delete again."""
    store = await _get(db, store_id)
    if body.confirm_name != store.name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Type the store's name exactly as it is shown to delete it")
    if store.status not in (StoreStatus.ready, StoreStatus.failed):
        # The worker is busy with it, and would mark it ready over the delete.
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait until the store is ready or has failed, then delete it")
    store.status, store.error = StoreStatus.deleting, None
    record(db, request, admin, "store.delete", store.slug, name=store.name, reason=body.reason)
    await db.commit()
    await db.refresh(store)
    return store


@router.get("", response_model=list[StoreOut])
async def list_stores(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Store]:
    return await stores_for_user(db, user)


@router.post("/{store_id}/members", response_model=StoreMemberOut, status_code=status.HTTP_201_CREATED)
async def assign_member(
    store_id: uuid.UUID,
    body: AssignMemberRequest,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StoreMember:
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")

    member = await db.get(User, body.user_id)
    if not member or not member.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if member.role == Role.admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The admin already has access to every store")

    assignment = StoreMember(store_id=store_id, user_id=body.user_id)
    db.add(assignment)
    record(db, request, admin, "store.member_add", member.email, store=store.slug)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Member is already in this store") from exc
    await db.refresh(assignment)

    background.add_task(
        send_email,
        member.email,
        *notice_email(member.name, "You were added to a Relay store", f"An admin gave you access to the store \"{store.name}\" ({store.url})."),
    )
    return assignment


@router.delete("/{store_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    store_id: uuid.UUID,
    user_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Takes effect immediately: the member's chat session and gateway API key
    lose access to the store on their next call (see stores_for_user).
    """
    result = await db.execute(
        select(StoreMember).where(StoreMember.store_id == store_id, StoreMember.user_id == user_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member is not assigned to this store")
    member, store = await db.get(User, user_id), await db.get(Store, store_id)
    await db.delete(assignment)
    record(
        db, request, admin, "store.member_remove",
        member.email if member else str(user_id), store=store.slug if store else str(store_id),
    )
    await db.commit()

    if member and store:
        background.add_task(
            send_email,
            member.email,
            *notice_email(member.name, "You were removed from a Relay store", f"An admin removed your access to the store \"{store.name}\"."),
        )


@router.get("/{store_id}/members", response_model=list[UserOut])
async def list_store_members(
    store_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")

    result = await db.execute(select(User).join(StoreMember, StoreMember.user_id == User.id).where(StoreMember.store_id == store_id))
    return list(result.scalars().all())
