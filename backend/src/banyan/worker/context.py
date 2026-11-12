"""What every handler may use."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.config import Settings
from banyan.llm import RoleClient
from banyan.steward import StewardClient
from banyan.tools.web import Web


@dataclass(frozen=True)
class WorkerContext:
    sessions: async_sessionmaker[AsyncSession]
    llm: RoleClient
    steward: StewardClient | None
    settings: Settings
    web: Web | None = None
