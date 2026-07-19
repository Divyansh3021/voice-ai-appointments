"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refdata_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resource_type", name="uq_refdata_resource_type"),
    )

    op.create_table(
        "patients_cache",
        sa.Column("phone_number", sa.String(length=32), primary_key=True),
        sa.Column("cliniko_patient_id", sa.BigInteger(), nullable=False),
        sa.Column("first_name", sa.String(length=255)),
        sa.Column("last_name", sa.String(length=255)),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_name", sa.String(length=255), nullable=False),
        sa.Column("branch_id", sa.String(length=64)),
        sa.Column("caller_number", sa.String(length=32)),
        sa.Column("patient_id", sa.BigInteger()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(length=32)),
        sa.Column("transcript_summary", sa.Text()),
        sa.Column("error_detail", sa.Text()),
    )

    op.create_table(
        "appointment_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id")),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("cliniko_appointment_id", sa.BigInteger()),
        sa.Column("request_payload", postgresql.JSONB()),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_payload", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("appointment_audit")
    op.drop_table("calls")
    op.drop_table("patients_cache")
    op.drop_table("refdata_cache")
