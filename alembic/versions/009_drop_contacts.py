"""Drop contacts table.

Revision ID: 009
Revises: 008
Create Date: 2026-05-04
"""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("contacts")


def downgrade() -> None:
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import UUID, ARRAY

    op.create_table(
        "contacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(200)),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(200)),
        sa.Column("topics", ARRAY(sa.String)),
        sa.Column("note", sa.Text),
        sa.Column("department_id", UUID(as_uuid=True), sa.ForeignKey("departments.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
