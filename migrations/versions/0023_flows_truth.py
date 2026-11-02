"""Join independently implemented process and annotation additions without rewriting either.

Both parents extend accepted schema0021. This merge changes no stored data.
"""

revision = "0023_flows_truth"
down_revision = ("0022_process", "0022_annotation")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
