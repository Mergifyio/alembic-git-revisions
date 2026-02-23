# alembic-git-revisions

Automatic [Alembic](https://alembic.sqlalchemy.org/) migration chaining based on git commit history.

## The problem

When multiple developers create Alembic migrations on separate branches, they often end up with the same `down_revision` — the current head at the time each branch was created. When these branches merge, Alembic fails with a `MultipleHeads` error because two migrations point to the same predecessor.

The usual fix is manual: rebase, update `down_revision`, and hope nobody else merges in the meantime.

## How it works

Instead of hardcoding `down_revision`, this library determines the migration chain automatically from git history. It uses `git log --reverse --diff-filter=A` to find the order in which migration files were first committed, then chains them linearly after the last "static" (hardcoded) migration.

This means:
- New migrations never conflict with each other
- The chain is always linear, regardless of branch merge order
- Existing migrations with hardcoded `down_revision` continue to work

## Installation

```bash
pip install alembic-git-revisions
```

## Setup

Copy the provided template to your Alembic `script.py.mako`:

```mako
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
```

A reference template is included in the package at `alembic_git_revisions/templates/script.py.mako`.

That's it. New migrations generated with `alembic revision --autogenerate` will automatically chain themselves using git history.

## Environments without git (Docker, CI)

In Docker images or CI environments where git history isn't available, pre-generate a `revision_chain.json` file before building:

```bash
# Using the CLI
alembic-git-revisions /path/to/alembic/versions

# Or as a Python module
python -m alembic_git_revisions /path/to/alembic/versions
```

This writes `revision_chain.json` next to the `versions/` directory. The library uses this file automatically when it exists, falling back to git when it doesn't.

**Important:** The git clone must have full history (`git clone` or `actions/checkout` with `fetch-depth: 0`). Shallow clones produce incorrect ordering.

Add `revision_chain.json` to your `.gitignore` — it should only exist in built artifacts.

## How migrations are classified

The library handles three types of migrations:

- **Dynamic** — uses `get_down_revision()`, chained automatically by git history
- **Static** — has a hardcoded `down_revision`, managed manually (legacy migrations)
- **Hybrid** — a static migration whose `down_revision` points to a dynamic one; participates in the dynamic ordering so the chain stays linear

## API

### `get_down_revision(revision, versions_dir=None)`

Returns the `down_revision` for the given revision ID. Auto-discovers the versions directory from the calling migration file's location. Pass `versions_dir` explicitly for non-standard setups or tests.

### `generate_chain_file(versions_dir)`

Generates `revision_chain.json` from git history. Run this before building Docker images.

### `build_chain(versions_dir)`

Returns the full `{revision: down_revision}` dict. Cached per `versions_dir`. Use `build_chain.cache_clear()` to reset in tests.

## License

Apache-2.0
