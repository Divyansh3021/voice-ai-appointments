"""transcript turns + call recording columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("recording_url", sa.Text()))
    op.add_column("calls", sa.Column("egress_id", sa.String(length=64)))

    op.create_table(
        "transcript_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transcript_turns_call_id", "transcript_turns", ["call_id"])


def downgrade() -> None:
    op.drop_index("ix_transcript_turns_call_id", table_name="transcript_turns")
    op.drop_table("transcript_turns")
    op.drop_column("calls", "egress_id")
    op.drop_column("calls", "recording_url")
