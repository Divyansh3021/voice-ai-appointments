class ClinikoError(Exception):
    """Base error for anything that goes wrong talking to Cliniko."""


class ClinikoNotFound(ClinikoError):
    pass


class ClinikoConflict(ClinikoError):
    """The slot/action collided with existing state (e.g. double-booked appointment)."""


class ClinikoRateLimited(ClinikoError):
    """We hit the 200 req/min budget and the bounded wait wasn't enough."""


class ClinikoBadRequest(ClinikoError):
    """The request itself was malformed (bad filter syntax, invalid date
    range, etc.) - a bug in how we called Cliniko, not something the caller
    did. Carries Cliniko's own message so it's debuggable from the logs."""


class ClinikoValidationError(ClinikoError):
    """422: the request was well-formed but failed a business-rule
    validation on Cliniko's side - e.g. cancellation_reason not matching a
    reason configured in that clinic's Cliniko settings. `body` carries the
    raw error text so callers can decide whether to fall back to something
    else (see cancel_appointment's DELETE fallback)."""

    def __init__(self, message: str, body: str = "") -> None:
        super().__init__(message)
        self.body = body
