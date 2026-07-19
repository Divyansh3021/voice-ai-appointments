"""Sets required env vars before any `clinic_agent` module is imported,
since config.Settings() is constructed at import time."""
import os

os.environ.setdefault("LIVEKIT_URL", "wss://test.livekit.cloud")
os.environ.setdefault("LIVEKIT_API_KEY", "test")
os.environ.setdefault("LIVEKIT_API_SECRET", "test")
os.environ.setdefault("CLINIKO_API_KEY", "testkey-shard1")
os.environ.setdefault("CLINIKO_CONTACT_EMAIL", "test@example.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT", "test-deployment")
os.environ.setdefault("AZURE_SPEECH_KEY", "test")
os.environ.setdefault("AZURE_SPEECH_REGION", "test")
os.environ.setdefault("SARVAM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
