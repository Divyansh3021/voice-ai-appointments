FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

RUN useradd --create-home appuser
USER appuser

# Pulls STT/VAD/etc model files LiveKit plugins need, baked into the image
# rather than downloaded on first call.
RUN python -m clinic_agent.entrypoint download-files || true

# Default command; overridden per-service in docker-compose.yml.
CMD ["python", "-m", "clinic_agent.entrypoint", "start"]
