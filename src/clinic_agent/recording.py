"""Call recording via LiveKit Egress -> Azure Blob Storage.

Entirely optional: no-ops if AZURE_STORAGE_ACCOUNT_NAME/KEY aren't set, and
never raises out of either function - a recording failure is worth logging
(it'll alert, per alerting.py) but should never take down the actual call.
"""
from __future__ import annotations

import logging

from livekit import api

from clinic_agent.config import settings

logger = logging.getLogger(__name__)


def _client() -> api.LiveKitAPI:
    return api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )


async def start_recording(room_name: str) -> tuple[str, str] | None:
    """Starts room-composite (mixed audio) egress to Azure Blob Storage.
    Returns (egress_id, recording_url) on success, None if recording isn't
    configured or if starting failed."""
    if not settings.recordings_configured:
        return None

    filepath = f"{room_name}.ogg"
    lkapi = _client()
    try:
        request = api.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            file_outputs=[
                api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=filepath,
                    azure=api.AzureBlobUpload(
                        account_name=settings.azure_storage_account_name,
                        account_key=settings.azure_storage_account_key,
                        container_name=settings.azure_storage_container,
                    ),
                )
            ],
        )
        info = await lkapi.egress.start_room_composite_egress(request)
        recording_url = (
            f"https://{settings.azure_storage_account_name}.blob.core.windows.net/"
            f"{settings.azure_storage_container}/{filepath}"
        )
        logger.info("recording started: egress_id=%s url=%s", info.egress_id, recording_url)
        return info.egress_id, recording_url
    except Exception:
        logger.exception("failed to start call recording for room %s", room_name)
        return None
    finally:
        await lkapi.aclose()


async def stop_recording(egress_id: str) -> None:
    if not settings.recordings_configured:
        return
    lkapi = _client()
    try:
        await lkapi.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
        logger.info("recording stopped: egress_id=%s", egress_id)
    except Exception:
        logger.exception("failed to stop egress %s", egress_id)
    finally:
        await lkapi.aclose()
