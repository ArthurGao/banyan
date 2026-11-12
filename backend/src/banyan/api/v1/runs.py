"""Generate all (design §5.1.1): estimate, start, progress, pause/resume/cancel, node retry."""

import dataclasses
import uuid

from fastapi import APIRouter, Request, status

from banyan.api.deps import Session
from banyan.api.schemas import EstimateOut, RunCreate, RunNode, RunOut, RunPatch
from banyan.models import GenerationRun
from banyan.services import runs, topics

router = APIRouter(tags=["generation"])


async def _out(s: Session, run: GenerationRun) -> RunOut:
    states = await runs.node_states(s, run.node_ids)
    return RunOut(
        id=run.id, topic_id=run.topic_id, status=run.status, token_budget=run.token_budget,
        tokens_used=await runs.tokens_used(s, run.id), estimate=EstimateOut(**run.estimate),
        nodes=[RunNode(node_id=n, status=states[n]) for n in run.node_ids],
        created_at=run.created_at,
    )  # fmt: skip


@router.get("/topics/{topic_id}/generation/estimate")
async def estimate(topic_id: uuid.UUID, request: Request, s: Session) -> EstimateOut:
    """What "generate all" would cost, shown before the learner starts it."""
    await topics.get_topic(s, topic_id)
    default = request.app.state.settings.explain_tokens_estimate
    plan = await runs.estimate(s, topic_id, default_tokens=default)
    return EstimateOut(**dataclasses.asdict(plan))


@router.post("/topics/{topic_id}/generation-runs", status_code=status.HTTP_201_CREATED)
async def start(topic_id: uuid.UUID, body: RunCreate, request: Request, s: Session) -> RunOut:
    """409 when the topic already has a run in progress."""
    settings = request.app.state.settings
    run = await runs.start_run(
        s, topic_id, token_budget=body.token_budget or settings.bulk_max_tokens,
        default_tokens=settings.explain_tokens_estimate,
    )  # fmt: skip
    return await _out(s, run)


@router.get("/generation-runs/{run_id}")
async def progress(run_id: uuid.UUID, s: Session) -> RunOut:
    return await _out(s, await runs.get_run(s, run_id))


@router.patch("/generation-runs/{run_id}")
async def change(run_id: uuid.UUID, body: RunPatch, s: Session) -> RunOut:
    run = await runs.change_run(s, run_id, action=body.action, add_budget=body.add_budget)
    return await _out(s, run)


@router.post("/generation-runs/{run_id}/nodes/{node_id}/retry")
async def retry_node(run_id: uuid.UUID, node_id: uuid.UUID, s: Session) -> RunOut:
    return await _out(s, await runs.retry_node(s, run_id, node_id))
