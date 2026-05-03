"""Wiki pivot — drop chunks/images/insights, add wiki_pages + wiki_links, source contributor + outline.

Revision ID: 006
Revises: 005
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Drop old RAG tables (cascade removes their indexes) ---
    op.drop_table("chunk_images")
    op.drop_table("source_insights")
    op.drop_table("source_chunks")

    # --- Drop embedding column from notes ---
    op.drop_column("notes", "embedding")

    # --- Add Source contribution + outline columns ---
    op.add_column(
        "sources",
        sa.Column(
            "contributed_by_employee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_sources_contributed_by_employee_id",
        "sources",
        ["contributed_by_employee_id"],
    )
    op.add_column(
        "sources",
        sa.Column("outline_json", JSONB, nullable=True),
    )
    # Char offsets (in full_text) where each extracted page begins. Lets the
    # MCP `get_source_pages` tool slice raw text by page range cheaply.
    op.add_column(
        "sources",
        sa.Column("page_offsets", JSONB, nullable=True),
    )

    # --- wiki_pages ---
    op.create_table(
        "wiki_pages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(300), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("page_type", sa.String(30), nullable=False),
        sa.Column("content_md", sa.Text, nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "knowledge_type_slugs",
            ARRAY(sa.String),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "source_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wiki_pages_page_type", "wiki_pages", ["page_type"])
    op.execute(
        "CREATE INDEX ix_wiki_pages_kt_slugs ON wiki_pages USING GIN (knowledge_type_slugs)"
    )
    op.execute(
        "CREATE INDEX ix_wiki_pages_source_ids ON wiki_pages USING GIN (source_ids)"
    )
    # HNSW for cosine semantic search (matches existing convention used on source_chunks)
    op.execute(
        """
        CREATE INDEX ix_wiki_pages_embedding_hnsw
        ON wiki_pages
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
    # Full-text search over content_md (mirror old chunk fulltext approach)
    op.execute(
        """
        CREATE INDEX ix_wiki_pages_fulltext
        ON wiki_pages
        USING GIN (to_tsvector('simple', content_md))
        """
    )

    # --- wiki_links (derived edges from [[wikilink]] parsing) ---
    op.create_table(
        "wiki_links",
        sa.Column("from_slug", sa.String(300), nullable=False),
        sa.Column("to_slug", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("from_slug", "to_slug"),
    )
    op.create_index("ix_wiki_links_from_slug", "wiki_links", ["from_slug"])
    op.create_index("ix_wiki_links_to_slug", "wiki_links", ["to_slug"])

    # --- Seed reserved pages: _index and _log ---
    op.execute(
        """
        INSERT INTO wiki_pages (slug, title, page_type, content_md, summary)
        VALUES
          ('_index', 'Wiki Index', 'index', '# Wiki Index\n\n_(empty — no pages yet)_\n', 'Catalog of all wiki pages'),
          ('_log',   'Wiki Log',   'log',   '# Wiki Log\n\n_(empty — no activity yet)_\n', 'Chronological activity log')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_wiki_links_to_slug", table_name="wiki_links")
    op.drop_index("ix_wiki_links_from_slug", table_name="wiki_links")
    op.drop_table("wiki_links")

    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_fulltext")
    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_source_ids")
    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_kt_slugs")
    op.drop_index("ix_wiki_pages_page_type", table_name="wiki_pages")
    op.drop_table("wiki_pages")

    op.drop_column("sources", "page_offsets")
    op.drop_column("sources", "outline_json")
    op.drop_index("ix_sources_contributed_by_employee_id", table_name="sources")
    op.drop_column("sources", "contributed_by_employee_id")

    # Restore notes.embedding
    op.add_column("notes", sa.Column("embedding", Vector(768), nullable=True))

    # Recreate dropped tables (minimal — no data restored)
    op.create_table(
        "source_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(768)),
        sa.Column("chunk_index", sa.Integer, server_default="0"),
        sa.Column("page_number", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_source_chunks_source_id", "source_chunks", ["source_id"])
    op.create_table(
        "source_insights",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("insight_type", sa.String(100)),
        sa.Column("content", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "chunk_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chunk_id", UUID(as_uuid=True), sa.ForeignKey("source_chunks.id", ondelete="CASCADE")),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("caption", sa.Text),
        sa.Column("page_number", sa.Integer),
        sa.Column("image_index", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chunk_images_chunk_id", "chunk_images", ["chunk_id"])
    op.create_index("ix_chunk_images_source_id", "chunk_images", ["source_id"])
