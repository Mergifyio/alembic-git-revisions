"""${message}

Revision ID: ${up_revision}
Create Date: ${create_date}

"""
from alembic import op
from alembic_git_revisions import get_down_revision
import sqlalchemy
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = get_down_revision(revision)
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
