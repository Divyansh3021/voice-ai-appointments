"""Small FastAPI backend alongside the LiveKit agent worker: health checks
for the container orchestrator, Twilio status webhooks for ops visibility,
and a couple of bearer-token-protected admin endpoints. This is what makes
"real backend" concrete without inventing a separate deploy pipeline - same
codebase, same Postgres as the agent worker, just a second process."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from clinic_agent.alerting import install_alert_handler
from clinic_agent.api.webhooks import router as webhooks_router
from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.config import settings
from clinic_agent.db.models import Call
from clinic_agent.db.repo import get_transcript
from clinic_agent.db.session import get_session
from clinic_agent.logging_setup import install_file_handler
from clinic_agent.refdata.sync import refresh_refdata

# Once at module import (this process has one event loop for its whole
# lifetime, unlike the agent worker - see reset_engine_for_current_loop's
# docstring for why that one's different).
install_alert_handler(settings.alert_webhook_url, source="api")
install_file_handler("api", log_dir=settings.log_dir)

app = FastAPI(title="Clinic Voice Agent API")
app.include_router(webhooks_router)

_bearer = HTTPBearer(auto_error=False)


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    if credentials is None or credentials.credentials != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/admin/calls", dependencies=[Depends(require_admin)])
async def list_calls(limit: int = 50) -> list[dict]:
    async with get_session() as session:
        result = await session.execute(select(Call).order_by(Call.started_at.desc()).limit(limit))
        calls = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "room_name": c.room_name,
            "branch_id": c.branch_id,
            "caller_number": c.caller_number,
            "outcome": c.outcome,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "ended_at": c.ended_at.isoformat() if c.ended_at else None,
            "transcript_summary": c.transcript_summary,
            "recording_url": c.recording_url,
        }
        for c in calls
    ]


@app.get("/admin/calls/{call_id}/transcript", dependencies=[Depends(require_admin)])
async def get_call_transcript(call_id: uuid.UUID) -> list[dict]:
    async with get_session() as session:
        turns = await get_transcript(session, call_id)
    return [
        {
            "role": t.role,
            "content": t.content,
            "metrics": t.metrics,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in turns
    ]


@app.post("/admin/refdata/refresh", dependencies=[Depends(require_admin)])
async def trigger_refdata_refresh() -> dict:
    client = ClinikoClient(settings.cliniko_api_key, settings.cliniko_contact_email)
    try:
        snapshot = await refresh_refdata(client)
    finally:
        await client.aclose()
    return {
        "businesses": len(snapshot.businesses),
        "practitioners": len(snapshot.practitioners),
        "appointment_types": len(snapshot.appointment_types),
    }
