"""FDA-002: initial schema — users, documents, analyses, usage

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-17

Creates the four core tables (fda_ prefix) for the Financial Document Analyzer.
Fully reversible: downgrade() drops the tables in FK-safe order.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Server-side UUID generation. gen_random_uuid() is built in to PostgreSQL 13+;
# pgcrypto is enabled as a fallback for older servers.
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── fda_users ────────────────────────────────────────────────────────────
    op.create_table(
        "fda_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("plan", sa.String(length=20), nullable=False, server_default="free"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_fda_users_email", "fda_users", ["email"], unique=True)

    # ── fda_documents ────────────────────────────────────────────────────────
    op.create_table(
        "fda_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="processing"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(
            ["user_id"], ["fda_users.id"],
            name="fk_fda_documents_user_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_fda_documents_user_id", "fda_documents", ["user_id"])

    # ── fda_analyses ─────────────────────────────────────────────────────────
    op.create_table(
        "fda_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_ocr", sa.Text(), nullable=True),
        sa.Column("structured_data", postgresql.JSONB(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=128), nullable=True),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(
            ["document_id"], ["fda_documents.id"],
            name="fk_fda_analyses_document_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_fda_analyses_document_id", "fda_analyses", ["document_id"])

    # ── fda_usage ────────────────────────────────────────────────────────────
    # One row per (user, month) tracks document_count for the freemium gate.
    op.create_table(
        "fda_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),  # "YYYY-MM"
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["fda_users.id"],
            name="fk_fda_usage_user_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "month", name="uq_fda_usage_user_month"),
    )


def downgrade() -> None:
    # Drop in reverse dependency order so foreign keys never block the drop.
    op.drop_table("fda_usage")
    op.drop_index("ix_fda_analyses_document_id", table_name="fda_analyses")
    op.drop_table("fda_analyses")
    op.drop_index("ix_fda_documents_user_id", table_name="fda_documents")
    op.drop_table("fda_documents")
    op.drop_index("ix_fda_users_email", table_name="fda_users")
    op.drop_table("fda_users")
