"""In-memory steward for tests (AGENTS.md rule 8). B0: health only; the rest joins with B3."""

from banyan.steward.client import StewardHealth


class FakeSteward:
    def __init__(self, health: StewardHealth = "ok") -> None:
        self.health_status = health

    async def health(self) -> StewardHealth:
        return self.health_status
