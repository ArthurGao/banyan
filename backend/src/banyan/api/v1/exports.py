"""The HTML study guide (design §10.2)."""

import uuid

from fastapi import APIRouter, status
from fastapi.responses import HTMLResponse

from banyan.api.deps import Session
from banyan.api.schemas import ExportOut
from banyan.errors import Conflict
from banyan.models import ExportStatus
from banyan.services import exports

router = APIRouter(tags=["export"])


@router.post("/topics/{topic_id}/export", status_code=status.HTTP_201_CREATED)
async def export_topic(topic_id: uuid.UUID, s: Session) -> ExportOut:
    """Renders the topic's notes now; `status` is done, or failed with `error`."""
    return ExportOut.model_validate(await exports.export_topic(s, topic_id))


@router.get(
    "/exports/{export_id}",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}, 409: {"description": "the export failed"}},
)
async def download(export_id: uuid.UUID, s: Session, download: bool = False) -> HTMLResponse:
    """The single-file HTML; `download=true` asks the browser to save it."""
    export = await exports.get_export(s, export_id)
    if export.status != ExportStatus.DONE or export.html is None:
        raise Conflict(f"the export {export.status.value}: {export.error or ''}".strip())
    headers = {"Content-Disposition": f'attachment; filename="study-guide-{export_id}.html"'}
    return HTMLResponse(export.html, headers=headers if download else None)
