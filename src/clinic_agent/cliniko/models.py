"""Thin pydantic views over the Cliniko JSON we actually use.

We deliberately model only the fields the agent needs, not the full Cliniko
resource shape - extra fields from the API are ignored rather than erroring.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClinikoModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _relation_id(value: object) -> int | None:
    """Cliniko represents relations as `{"links": {"self": ".../resource/123"}}`
    rather than a flat id. Pull the trailing integer out of that URL. Accepts
    a bare int too, so callers that already have a flat id keep working."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        href = value.get("links", {}).get("self", "")
        match = re.search(r"/(\d+)(?:\?.*)?$", href)
        return int(match.group(1)) if match else None
    return None


def _flatten_relation_ids(data: dict, fields: dict[str, str]) -> dict:
    """For each (flat_field, relation_field) pair, if flat_field is missing
    but relation_field is present, derive the flat id from the relation."""
    data = dict(data)
    for flat_field, relation_field in fields.items():
        if data.get(flat_field) is None and relation_field in data:
            data[flat_field] = _relation_id(data[relation_field])
    return data


class Business(ClinikoModel):
    # Cliniko's field is "business_name", not "name" - populate_by_name lets
    # this validate from a live API response (business_name) *or* from our
    # own round-tripped refdata_cache JSON (which will have "name", since
    # that's what model_dump() writes out for this field).
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str = Field(validation_alias="business_name")
    # e.g. "Asia/Kolkata" - needed to speak times in the clinic's local
    # timezone rather than the UTC instants Cliniko's API deals in.
    time_zone_identifier: str = "UTC"


class Practitioner(ClinikoModel):
    id: int
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class AppointmentType(ClinikoModel):
    id: int
    name: str
    duration_in_minutes: int | None = None


class PhoneNumber(ClinikoModel):
    number: str
    phone_type: str | None = None


class Patient(ClinikoModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: str | None = None
    patient_phone_numbers: list[PhoneNumber] = []


class AvailableTime(ClinikoModel):
    appointment_start: datetime


class Appointment(ClinikoModel):
    id: int
    starts_at: datetime
    ends_at: datetime | None = None
    patient_id: int | None = None
    practitioner_id: int | None = None
    business_id: int | None = None
    appointment_type_id: int | None = None
    cancelled_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_relation_ids(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        return _flatten_relation_ids(
            data,
            {
                "patient_id": "patient",
                "practitioner_id": "practitioner",
                "business_id": "business",
                "appointment_type_id": "appointment_type",
            },
        )
