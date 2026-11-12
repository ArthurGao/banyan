"""Loading a row by id, or a 404 (design §9 errors)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from banyan.errors import NotFound
from banyan.models import Base


async def get_or_404[M: Base](s: AsyncSession, model: type[M], id_: uuid.UUID) -> M:
    row = await s.get(model, id_)
    if row is None:
        raise NotFound(f"{model.__tablename__.removesuffix('s')} {id_} not found")
    return row
