# Clinic Voice Agent

A bilingual (English/Hindi/Hinglish) voice AI receptionist for a multi-branch
clinic, built on [LiveKit Agents](https://docs.livekit.io/agents/) and
[Cliniko](https://www.cliniko.com/). Patients call one phone number, and the
agent books, reschedules, or cancels appointments against real Cliniko data
with no human involved.

See `../clinic-voice-agent` design rationale in the project plan this was
built from for the "why" behind each choice below.

## Architecture at a glance

- **Telephony**: one Twilio number → Twilio Elastic SIP Trunk → LiveKit
  Cloud SIP → a single LiveKit dispatch rule spins up a room + agent job per
  call.
- **Agent**: one LiveKit `Agent` (`src/clinic_agent/agent.py`) with ~10
  `function_tool`s (`src/clinic_agent/tools/`) - no multi-agent handoff
  layer.
- **Pipeline**: Silero VAD + LiveKit's multilingual turn-detector, Sarvam
  STT (primary, for Hinglish) with Azure STT fallback, Azure OpenAI (your
  deployed chat model), Azure TTS
  (primary) with Sarvam fallback.
- **Cliniko integration**: `src/clinic_agent/cliniko/` (thin REST client +
  booking/availability call sequences), backed by a 30-minute-refreshed
  in-memory cache of branches/doctors/appointment types
  (`src/clinic_agent/refdata/`) so mid-call browsing never hits Cliniko's
  200 req/min limit.
- **Backend + datastore**: Postgres (`src/clinic_agent/db/`) for call logs,
  a full per-turn transcript, an appointment audit trail, and a
  phone→patient cache; a small FastAPI app (`src/clinic_agent/api/`) for
  health checks, Twilio webhooks, and admin endpoints.
- **Observability**: every module logs through the `clinic_agent` logger
  namespace with `[call=<id>]`-tagged lines you can grep out of a noisy
  multi-call log; any `ERROR`+ log line anywhere becomes a webhook alert
  (`src/clinic_agent/alerting.py`) if `ALERT_WEBHOOK_URL` is set; call audio
  is optionally recorded to Azure Blob Storage via LiveKit Egress
  (`src/clinic_agent/recording.py`) if `AZURE_STORAGE_*` is set.

## Local setup

Datastore is [Neon Postgres](https://neon.tech) (serverless, no local
Postgres container to run) - create a project there first, then:

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on Linux/Mac
pip install -e ".[dev]"
cp .env.example .env            # fill in real keys, including your Neon DATABASE_URL
alembic upgrade head
python scripts/seed_refdata.py  # prime the branches/doctors/appointment-types cache
```

See `.env.example` for the exact `DATABASE_URL` format Neon needs (their
dashboard gives you a `sslmode=require` psycopg-style string; swap that for
`ssl=require` since we use the `asyncpg` driver).

Run the tests (no external services needed - Cliniko calls are mocked with
`respx`):

```bash
pytest
```

Run the agent locally against LiveKit's Playground, over WebRTC, no
telephony involved yet:

```bash
python -m clinic_agent.entrypoint dev
```

## Going live on a real number

1. **Cliniko**: set up the 30-day trial account with your real branches
   (Businesses), doctors (Practitioners), and services (Appointment Types).
   Grab an API key from Cliniko's settings.
2. **Validate the day-one unknowns** (see plan's Open Questions) against
   your real trial account before trusting the agent with live callers:
   ```bash
   python scripts/seed_refdata.py
   ```
   Check the printed practitioner/appointment-type lists look right - this
   also exercises the practitioner↔appointment-type association fetch,
   which is the one part of the Cliniko integration inferred rather than
   doc-confirmed.
3. **LiveKit + Twilio**:
   ```bash
   ./scripts/setup_livekit_sip.sh +1XXXXXXXXXX
   ```
   Then create a Twilio Elastic SIP Trunk, set its Origination URL to your
   LiveKit Cloud SIP URI, and attach your number to it. Get the SIP URI from
   the LiveKit Cloud dashboard's **Telephony** page (top of the page) - it's
   an independently-assigned hostname (e.g. `sip:11ifxjskywa.sip.livekit.cloud`),
   **not** derived from your project's `LIVEKIT_URL` subdomain.
4. **Deploy**:
   ```bash
   docker compose up -d --build
   ```
   (adjust for your actual hosting - `docker-compose.yml` is the reference
   for what needs to run: one-shot `migrate`, `agent-worker`, `api`, all
   pointed at your Neon `DATABASE_URL`.)
5. **Test call**: call the number, book an appointment, and confirm it
   shows up correctly in the Cliniko dashboard. Then test a reschedule and
   a cancel the same way. `GET /admin/calls` (bearer token = `ADMIN_API_TOKEN`)
   shows the call log if anything needs debugging.

## Observability: logs, transcripts, recordings, alerts

**Logs**: every tool call and Cliniko operation logs at INFO through the
`clinic_agent` logger namespace, tagged `[call=<uuid>]` where the record
belongs to a specific call - `grep '\[call=<id>\]'` a log dump to pull one
call's full trace out of a busy worker. Persisted to rotating files under
`LOG_DIR` (default `./logs/`) - `agent-worker.log` and `api.log`, one per
process, 10MB × 5 files each before the oldest rolls off. Captures
`livekit.agents`' own logging too (STT/TTS timing, tool dispatch), not just
`clinic_agent.*` - a call's full story spans both. In Docker, mount a
volume at `/app/logs` (already set up in `docker-compose.yml`) so these
survive a container restart.

**Transcripts**: every conversation turn is written to the `transcript_turns`
table as it happens (not buffered until the call ends), independent of the
LLM's own end-of-call summary in `calls.transcript_summary` - the model's
self-reported summary can be wrong or incomplete, so this is the ground
truth of what was actually said. Fetch one via:
```
GET /admin/calls/{call_id}/transcript   (bearer token = ADMIN_API_TOKEN)
```

**Call recordings** (optional - skipped entirely if unconfigured): mixed
call audio is uploaded to Azure Blob Storage via LiveKit Egress, with the
resulting URL stored on `calls.recording_url`. To enable:
1. Azure Portal → **Create a resource → Storage account**. Any redundancy
   tier is fine for this (locally-redundant is cheapest).
2. Inside the storage account → **Containers** → **+ Container** → name it
   `call-recordings` (or whatever you set `AZURE_STORAGE_CONTAINER` to).
   Private access level is fine - LiveKit uploads via the account key, not
   a public URL.
3. Storage account → **Access keys** → copy the **Storage account name**
   and **Key** (key1 is fine).
4. Fill `AZURE_STORAGE_ACCOUNT_NAME` / `AZURE_STORAGE_ACCOUNT_KEY` /
   `AZURE_STORAGE_CONTAINER` in `.env`. Recording starts automatically for
   every call the moment these are set - no code change needed.

**Alerts** (optional - skipped entirely if unconfigured): set
`ALERT_WEBHOOK_URL` to any endpoint that accepts a JSON `POST`
(`{"source","level","logger","message","timestamp"}`) - a Zapier/n8n/Make
webhook trigger is the fastest way to turn that into a Slack message, SMS,
or email without writing a receiving service yourself. Every `ERROR`-level
log line anywhere in the app becomes an alert automatically (deduped per
identical message within a 5-minute window, capped at 30/hour so a crash
loop can't spam you) - nothing else to wire up per failure site.

## Known unverified assumptions

A few Cliniko API specifics couldn't be confirmed from public docs alone
and are implemented with a graceful fallback - see inline comments at each
site:

- Patient search by phone number (`tools/identify.py`) - falls back to
  name + date-of-birth if the phone filter doesn't behave as expected.
- Practitioner↔appointment-type association endpoint (`refdata/sync.py`) -
  falls back to "bookable for anything" per doctor if the endpoint 404s.
- Reschedule via `PATCH` vs. cancel+recreate (`tools/manage.py`) - both
  paths are implemented; whichever Cliniko actually needs, the other is
  the safety net.

Run `python scripts/seed_refdata.py` and a real test booking/reschedule/
cancel cycle against your trial account early to confirm which paths
actually fire.
