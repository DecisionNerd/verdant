"""Bind workflow dispatch to deployment identity

Revision ID: 0011_workflow_deployment
Revises: 0010_workflow_inbox
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_workflow_deployment"
down_revision = "0010_workflow_inbox"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_dispatches", sa.Column("deployment_id", sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column("workflow_dispatches", "deployment_id")
