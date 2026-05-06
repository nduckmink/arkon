"""Add AI Skills with scoped permission support and M2M departments.

Revision ID: 012
Revises: 011_permission_v2
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011_permission_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create skill_status enum type (check if exists first)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_enums = [e['name'] for e in inspector.get_enums()]
    
    if "skill_status" not in existing_enums:
        skill_status = postgresql.ENUM("active", "processing", "deleting", "deprecated", "archived", name="skill_status")
        skill_status.create(bind)

    # 2. Create skills table with scoping columns (No department_id here anymore)
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("slug", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("version_hash", sa.String(64), nullable=True),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("active", "processing", "deleting", "deprecated", "archived", name="skill_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        # Scoping columns (RBAC v2)
        sa.Column("scope_type", sa.String(length=20), nullable=False, server_default="global"),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_skills_slug", "skills", ["slug"], unique=True)

    # 3. Create skill_versions table
    op.create_table(
        "skill_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("version_hash", sa.String(64), nullable=True),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column("changelog", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"])

    # 4. Create skill_departments join table (M2M)
    op.create_table(
        'skill_departments',
        sa.Column('skill_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('department_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('skill_id', 'department_id')
    )


def downgrade() -> None:
    op.drop_table('skill_departments')
    op.drop_index("ix_skill_versions_skill_id", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index("ix_skills_slug", table_name="skills")
    op.drop_table("skills")
    
    # Drop enum type
    bind = op.get_bind()
    skill_status = postgresql.ENUM("active", "processing", "deleting", "deprecated", "archived", name="skill_status")
    skill_status.drop(bind)
