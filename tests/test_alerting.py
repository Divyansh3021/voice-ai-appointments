import logging

from clinic_agent.alerting import WebhookAlertHandler


def _make_handler(monkeypatch) -> tuple[WebhookAlertHandler, list[dict]]:
    """A handler with no background worker thread and its network call
    replaced by an in-memory list, so dedup/rate-limit logic can be tested
    synchronously and deterministically."""
    sent: list[dict] = []
    handler = WebhookAlertHandler("http://example.invalid/webhook", source="test", start_worker=False)
    monkeypatch.setattr(handler, "_post", lambda payload: sent.append(payload))
    return handler, sent


def _emit(handler: WebhookAlertHandler, logger_name: str, message: str) -> None:
    record = logging.LogRecord(
        name=logger_name, level=logging.ERROR, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    handler.emit(record)
    # emit() only enqueues; drain synchronously since we're not running the
    # handler's background worker thread in these tests.
    while not handler._queue.empty():
        payload = handler._queue.get_nowait()
        if not handler._rate_limited():
            handler._post(payload)


def test_identical_message_is_deduped_within_window(monkeypatch):
    handler, sent = _make_handler(monkeypatch)
    _emit(handler, "clinic_agent.tools.booking", "Cliniko rejected the availability request")
    _emit(handler, "clinic_agent.tools.booking", "Cliniko rejected the availability request")
    assert len(sent) == 1


def test_different_messages_both_send(monkeypatch):
    handler, sent = _make_handler(monkeypatch)
    _emit(handler, "clinic_agent.tools.booking", "error A")
    _emit(handler, "clinic_agent.tools.manage", "error B")
    assert len(sent) == 2


def test_rate_limit_caps_total_alerts_per_hour(monkeypatch):
    handler, sent = _make_handler(monkeypatch)
    for i in range(50):
        _emit(handler, "clinic_agent.x", f"distinct error {i}")
    assert len(sent) <= 30  # MAX_ALERTS_PER_HOUR


def test_payload_shape(monkeypatch):
    handler, sent = _make_handler(monkeypatch)
    _emit(handler, "clinic_agent.tools.booking", "something broke")
    assert len(sent) == 1
    payload = sent[0]
    assert payload["source"] == "test"
    assert payload["level"] == "ERROR"
    assert payload["logger"] == "clinic_agent.tools.booking"
    assert "something broke" in payload["message"]
    assert "timestamp" in payload
