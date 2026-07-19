"""Environment-driven configuration. One source of truth, no hardcoded secrets."""
from __future__ import annotations

import json

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Branch(BaseModel):
    id: str
    name: str
    cliniko_business_id: int


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LiveKit
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

    # Cliniko
    cliniko_api_key: str
    cliniko_contact_email: str

    # AI providers
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_deployment: str
    azure_openai_api_version: str = "2024-10-21"
    azure_speech_key: str
    azure_speech_region: str
    # Not used while STT/TTS run on Azure only - kept optional so it doesn't
    # block startup before Sarvam is wired back in as a fallback.
    sarvam_api_key: str = ""

    # Datastore
    database_url: str

    # Branches, as a JSON string in the env
    clinic_branches: str = "[]"

    # Admin API
    admin_api_token: str = "change-me"

    # Observability - all optional, features degrade gracefully when unset
    alert_webhook_url: str = ""  # generic JSON POST target for ERROR+ log alerts
    log_dir: str = "logs"  # rotating log file location, per-process files within it
    azure_storage_account_name: str = ""  # call recording upload target
    azure_storage_account_key: str = ""
    azure_storage_container: str = "call-recordings"

    @property
    def recordings_configured(self) -> bool:
        return bool(self.azure_storage_account_name and self.azure_storage_account_key)

    @property
    def branches(self) -> list[Branch]:
        return [Branch(**b) for b in json.loads(self.clinic_branches)]


settings = Settings()
