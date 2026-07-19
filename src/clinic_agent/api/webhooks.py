from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger("clinic_agent.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/twilio-status")
async def twilio_status(request: Request) -> dict:
    """Twilio call-status callback, for ops visibility only - the actual
    call handling happens over the SIP trunk into LiveKit, not through this
    webhook. Logs the event so a failed/no-answer call shows up somewhere
    even if it never reached the LiveKit agent."""
    form = await request.form()
    logger.info("twilio call status: %s", dict(form))
    return {"ok": True}
