"""Store creation is a STUB for this phase: it writes a DB row and returns a
fake URL. No Kubernetes/Helm provisioning happens here — see
IMPLEMENTATION.md for what a real Create Store Service will do instead.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin, user_rate_limit
from app.db.models import Role, Store, StoreMember, StoreStatus, User
from app.db.session import get_db
from app.schemas.auth import UserOut
from app.schemas.stores import AssignMemberRequest, CreateStoreRequest, StoreMemberOut, StoreOut
from app.services.admin_audit import record
from app.services.mailer import notice_email, send_email
from app.services.stores import stores_for_user, unique_slug

router = APIRouter(
    prefix="/stores",
    tags=["stores"],
    dependencies=[Depends(get_current_user), Depends(user_rate_limit(capacity=60, refill_per_minute=60))],
)


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(
    body: CreateStoreRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Store:
    # Retry a few times: unique_slug's check-then-insert has a race window
    # where two concurrent creates with the same name could pick the same
    # slug. A collision here is rare and cheap to retry against.
    for attempt in range(3):
        slug = await unique_slug(db, body.name)
        store = Store(
            name=body.name,
            slug=slug,
            status=StoreStatus.ready,  # stub: real provisioning will start at "pending"
            url=f"https://{slug}.stores.local",
            created_by=admin.id,
        )
        db.add(store)
        record(db, request, admin, "store.create", slug, name=body.name)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()  # the record goes with it; the next attempt adds its own
            if attempt == 2:
                raise HTTPException(status.HTTP_409_CONFLICT, "Could not allocate a unique store slug, try again")
            continue
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
        *notice_email(member.name, "You were added to a Urumi store", f"An admin gave you access to the store \"{store.name}\" ({store.url})."),
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
            *notice_email(member.name, "You were removed from a Urumi store", f"An admin removed your access to the store \"{store.name}\"."),
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
