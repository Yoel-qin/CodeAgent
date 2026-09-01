"""文档三表：documents / doc_sections / media_chunks（M2）。

Revision ID: v2_0002
Revises: v2_0001
Create Date: 2026-09-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "v2_0002"
down_revision = "v2_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("doc_name", sa.String(512), nullable=False),
        sa.Column("module", sa.String(256), nullable=True),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("minio_key", sa.String(1024), nullable=True),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("parse_meta", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_repo", "documents", ["repo"])
    op.create_unique_constraint(
        "uk_documents_repo_doc_name", "documents", ["repo", "doc_name"]
    )

    op.create_table(
        "doc_sections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("anchor", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("level", sa.Integer, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("order_index", sa.Integer, nullable=False),
        sa.Column("page", sa.Integer, nullable=True),
        sa.Column("embedding_synced", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_doc_sections_repo", "doc_sections", ["repo"])
    op.create_index("ix_doc_sections_document_id", "doc_sections", ["document_id"])

    op.create_table(
        "media_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("minio_key", sa.String(1024), nullable=True),
        sa.Column("page", sa.Integer, nullable=True),
        sa.Column("bbox", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("media_chunks")
    op.drop_table("doc_sections")
    op.drop_table("documents")
