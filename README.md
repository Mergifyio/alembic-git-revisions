# alembic-git-revisions

Automatic [Alembic](https://alembic.sqlalchemy.org/) migration chaining based on git commit history. No more `Multiple head revisions are present for given argument 'head'`.

## The problem

You merged two branches and Alembic now refuses to run:

```
ERROR [alembic.util.messaging] Multiple head revisions are present for given argument 'head'; please specify a specific target revision, '<branchname>@head' to narrow to a specific head, or 'heads' for all heads
```

When multiple developers create Alembic migrations on separate branches, they often end up with the same `down_revision` — the current head at the time each branch was created. When these branches merge, Alembic fails with this `MultipleHeads` error because two migrations point to the same predecessor.

The usual fix is manual: rebase, update `down_revision`, and hope nobody else merges in the meantime.

## How it works

Instead of hardcoding `down_revision`, this library determines the migration chain automatically from git history. It uses `git log --reverse --diff-filter=A` to find the order in which migration files were first committed, then chains them linearly after the last "static" (hardcoded) migration.

This means:
- New migrations never conflict with each other
- The chain is always linear, regardless of branch merge order
- Existing migrations with hardcoded `down_revision` continue to work, as long as they are not chained behind a revision that has already been applied somewhere (see [Hardcoded `down_revision` on a deployed database](#hardcoded-down_revision-on-a-deployed-database))

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

Classification reads the `revision` and `down_revision` attributes from each migration module (the same values Alembic loads), so any Alembic `file_template` and any `rev_id` format work.

## Hardcoded `down_revision` on a deployed database

A hybrid is placed immediately after the revision it points at, so any dynamic migration added between the two is re-parented onto the hybrid. That is what keeps the chain linear when two branches fork from the same head, and it is safe while none of those migrations has run yet.

It stops being safe once one of them has been applied. Say `bbbb -> cccc -> dddd` are already deployed, and a new migration hardcodes `down_revision = "bbbb"`:

```
chain before:  aaaa -> bbbb -> cccc -> dddd          alembic_version = dddd
chain after:   aaaa -> bbbb -> eeee -> cccc -> dddd  alembic_version = dddd
```

`eeee` now sits behind the recorded head. `alembic upgrade head` walks down from `dddd`, finds every revision already applied, and does nothing. The migration never runs, and nothing reports an error.

**The rule: only hardcode `down_revision` onto the current head.** Anything older is spliced into history the database has already walked past. Prefer `get_down_revision(revision)`, which cannot pick a stale parent.

To check an existing tree:

```bash
alembic-git-revisions --check /path/to/versions
```

This lists every hybrid chained ahead of earlier revisions and **exits 0**, because git history alone cannot prove a finding is a real problem. A hybrid added by a branch that forked from the same head produces exactly the same shape, and that case is benign. Treat the output as something to look at, not as a failure.

Only the revisions a database has actually applied separate the two. Pass them in to narrow the report to migrations that provably cannot run, which **exits non-zero**:

```bash
psql -Atc 'select version_num from alembic_version' \
  | alembic-git-revisions --check --applied - /path/to/versions
```

That is the form worth gating CI on. Note `alembic_version` records only the current head, so for a full picture supply every revision reachable from it. The same distinction is available from Python:

```python
from alembic_git_revisions import find_displaced_revisions

# advisory: includes the benign fork-from-the-same-head case
candidates = find_displaced_revisions(versions_dir)

# confirmed: only what cannot run on this database
for found in find_displaced_revisions(versions_dir, applied=applied):
    print(f"{found.hybrid} will never run on this database")
```

## API

### `get_down_revision(revision, versions_dir=None)`

Returns the `down_revision` for the given revision ID. Auto-discovers the versions directory from the calling migration file's location. Pass `versions_dir` explicitly for non-standard setups or tests.

### `generate_chain_file(versions_dir)`

Generates `revision_chain.json` from git history. Run this before building Docker images.

### `build_chain(versions_dir)`

Returns the full `{revision: down_revision}` dict. Cached per `versions_dir`. Use `build_chain.cache_clear()` to reset in tests.

### `parse_versions_dir(versions_dir)`

Returns the migrations in `versions_dir` as a list of `MigrationFile`, so tooling can inspect classification and ordering without building a chain.

The order is the raw order files were added to git. It is **not** the order the chain walks: `build_chain` re-parents a hybrid to sit immediately after the revision it hardcodes, which can move it far from its own add position. Use `build_chain` when you want traversal order.

Unlike `build_chain`, this never falls back to `revision_chain.json`, because that file records only `{revision: down_revision}` and carries neither classification nor ordering. Git is required, and its absence raises `RuntimeError` rather than returning a plausible wrong order. Results are not cached.

### `MigrationFile`

A frozen dataclass describing one parsed migration:

| Field | Meaning |
|---|---|
| `revision` | the revision id, read from the module's `revision` attribute |
| `filename` | the file's basename |
| `git_sequence` | position within the parse that produced it (see below) |
| `is_dynamic` | whether `down_revision` calls `get_down_revision()` |
| `static_down_revisions` | hardcoded parents; more than one means a merge migration |

`git_sequence` is a position within one particular parse, not a stable property of the file. Files absent from git history all share the same end-of-list sentinel and are separated only by `filename`, which is why `parse_versions_dir` sorts on both.

### `find_displaced_revisions(versions_dir, applied=None)`

Returns a list of `DisplacedRevision(hybrid, target, displaced)`, one per hybrid that is chained ahead of revisions added before it. Without `applied` the result is advisory and includes the benign fork-from-the-same-head case. Pass `applied`, the revisions a database has actually run, to keep only the hybrids that can never execute on it. See [Hardcoded `down_revision` on a deployed database](#hardcoded-down_revision-on-a-deployed-database).

### `CHAIN_FILENAME`

Name of the generated chain file, `revision_chain.json`. Use it instead of hardcoding the string when locating or cleaning up the generated artifact.

## License

Apache-2.0
