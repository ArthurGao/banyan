"""`/v1`: every route here requires the API token (api/deps.py)."""

from fastapi import APIRouter, Depends

from banyan.api.deps import require_token
from banyan.api.v1 import branches, exports, nodes, runs, topics

router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
router.include_router(topics.router)
router.include_router(nodes.router)
router.include_router(branches.router)
router.include_router(runs.router)
router.include_router(exports.router)
