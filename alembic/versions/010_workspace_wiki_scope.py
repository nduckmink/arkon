"""Change wiki_pages unique constraint: slug → (slug, scope_type, scope_id).

Allows the same slug to exist in different scopes (global vs workspace).
Uses COALESCE on scope_id to handle NULLs in the unique index.

Revision ID: 010
Revises: 009
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old unique constraint on slug alone
    op.drop_constraint("wiki_pages_slug_key", "wiki_pages", type_="unique")

    # Create a unique index that handles NULL scope_id via COALESCE
    op.execute(
        """
        CREATE UNIQUE INDEX uq_wiki_pages_slug_scope
        ON wiki_pages (slug, scope_type, COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_wiki_pages_slug_scope")
    op.create_unique_constraint("wiki_pages_slug_key", "wiki_pages", ["slug"])
