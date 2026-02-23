"""Automatic Alembic migration chaining based on git commit history.

Instead of hardcoding ``down_revision`` in each migration file, this library
determines the revision chain from the order files were committed to git.

Usage in ``script.py.mako``::

    from alembic_git_revisions import get_down_revision

    revision = ${repr(up_revision)}
    down_revision = get_down_revision(revision)

Generate a chain file for environments without git (Docker, CI)::

    alembic-git-revisions /path/to/versions
"""

from __future__ import annotations

import pathlib
import sys

from alembic_git_revisions._chain import (
    build_chain as build_chain,
)
from alembic_git_revisions._chain import (
    generate_chain_file as generate_chain_file,
)
from alembic_git_revisions._chain import (
    get_down_revision as get_down_revision,
)


def _cli() -> None:
    """CLI entry point: generate revision_chain.json."""
    if len(sys.argv) != 2:  # noqa: PLR2004
        print(  # noqa: T201
            f"Usage: {sys.argv[0]} <versions-directory>",
            file=sys.stderr,
        )
        sys.exit(1)

    generate_chain_file(pathlib.Path(sys.argv[1]))
