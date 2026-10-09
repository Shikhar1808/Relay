"""Store creation is a STUB for this phase: it writes a DB row and returns a
fake URL. No Kubernetes/Helm provisioning happens here — see
IMPLEMENTATION.md for what a real Create Store Service will do instead.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin, user_rate_limit
from app.models import Role, Store, StoreMember, StoreStatus, User
from app.schemas import AssignMemberRequest, CreateStoreRequest, StoreMemberOut, StoreOut, UserOut
from app.utils import unique_slug

router = APIRouter(
    prefix="/stores",
    tags=["stores"],
    dependencies=[Depends(get_current_user), Depends(user_rate_limit(capacity=60, refill_per_minute=60))],
)


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(
    body: CreateStoreRequest,
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
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
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
    if user.role == Role.admin:
        result = await db.execute(select(Store).order_by(Store.created_at))
    else:
        result = await db.execute(
            select(Store)
            .join(StoreMember, StoreMember.store_id == Store.id)
            .where(StoreMember.user_id == user.id)
            .order_by(Store.created_at)
        )
    return list(result.scalars().all())


@router.post("/{store_id}/members", response_model=StoreMemberOut, status_code=status.HTTP_201_CREATED)
async def assign_member(
    store_id: uuid.UUID,
    body: AssignMemberRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StoreMember:
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")

    member = await db.get(User, body.user_id)
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    assignment = StoreMember(store_id=store_id, user_id=body.user_id)
    db.add(assignment)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Member already assigned to store") from exc
    await db.refresh(assignment)
    return assignment


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
