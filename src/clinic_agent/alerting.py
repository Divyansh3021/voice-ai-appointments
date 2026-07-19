"""Webhook alerting: turns any ERROR+ log line anywhere under the
`clinic_agent` logger namespace into a webhook notification, with no need
to hand-wire an alert call into every failure site - every `logger.error`/
`logger.exception` already written throughout this codebase becomes an
alert for free the moment a webhook URL is configured.

Runs the actual HTTP POST on a background thread via a queue, never on the
calling thread - `Handler.emit()` is called synchronously wherever a log
statement fires, which in this codebase is almost always the asyncio event
loop thread. A blocking network call there would stall the live call audio
pipeline, which is worse than the outage we're trying to report.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request

DEDUP_WINDOW_SECONDS = 300  # identical (logger, message) pairs collapse into one alert per 5 min
MAX_ALERTS_PER_HOUR = 30  # hard cap so a crash loop can't spam the webhook indefinitely
_RATE_LIMIT_WINDOW_SECONDS = 3600


class WebhookAlertHandler(logging.Handler):
    def __init__(self, webhook_url: str, source: str, *, start_worker: bool = True) -> None:
        super().__init__(level=logging.ERROR)
        self._webhook_url = webhook_url
        self._source = source
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=1000)
        self._last_sent: dict[tuple[str, str], float] = {}
        self._sent_timestamps: list[float] = []
        # start_worker=False is for tests: draining the queue from a second
        # thread racing the test's own assertions makes this handler
        # otherwise impossible to test deterministically.
        if start_worker:
            self._worker = threading.Thread(target=self._run_worker, daemon=True, name="webhook-alert-worker")
            self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()

        dedup_key = (record.name, record.getMessage())
        now = time.monotonic()
        last = self._last_sent.get(dedup_key)
        if last is not None and now - last < DEDUP_WINDOW_SECONDS:
            return
        self._last_sent[dedup_key] = now

        payload = {
            "source": self._source,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "timestamp": time.time(),
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass  # drop rather than block; a full queue means we're already spamming

    def _run_worker(self) -> None:
        while True:
            payload = self._queue.get()
            if self._rate_limited():
                continue
            self._post(payload)

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        self._sent_timestamps = [t for t in self._sent_timestamps if now - t < _RATE_LIMIT_WINDOW_SECONDS]
        if len(self._sent_timestamps) >= MAX_ALERTS_PER_HOUR:
            return True
        self._sent_timestamps.append(now)
        return False

    def _post(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.URLError:
            pass  # don't let a broken webhook itself generate more log noise/alerts


def install_alert_handler(webhook_url: str, source: str) -> None:
    """Call once per process. `source` distinguishes which process/service
    an alert came from (e.g. "agent-worker" vs "api") since both attach to
    the same webhook."""
    if not webhook_url:
        return
    logger = logging.getLogger("clinic_agent")
    logger.addHandler(WebhookAlertHandler(webhook_url, source))
