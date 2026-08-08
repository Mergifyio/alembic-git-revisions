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

import argparse
import pathlib
import sys

from alembic_git_revisions._chain import (
    CHAIN_FILENAME as CHAIN_FILENAME,
)
from alembic_git_revisions._chain import (
    DisplacedRevision as DisplacedRevision,
)
from alembic_git_revisions._chain import (
    MigrationFile as MigrationFile,
)
from alembic_git_revisions._chain import (
    build_chain as build_chain,
)
from alembic_git_revisions._chain import (
    find_displaced_revisions as find_displaced_revisions,
)
from alembic_git_revisions._chain import (
    generate_chain_file as generate_chain_file,
)
from alembic_git_revisions._chain import (
    get_down_revision as get_down_revision,
)
from alembic_git_revisions._chain import (
    parse_versions_dir as parse_versions_dir,
)


def _read_applied(source: str) -> set[str]:
    """Read applied revision ids, one per line, from a file or stdin (``-``).

    Blank lines and ``#`` comments are ignored, so the output of a query
    against ``alembic_version`` can be piped in directly.
    """
    text = (
        sys.stdin.read()
        if source == "-"
        else pathlib.Path(source).read_text(encoding="utf-8")
    )
    return {
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    }


def _report_candidates(displaced: list[DisplacedRevision]) -> int:
    """Report hybrids that *may* be unreachable. Always exit code 0.

    Without the set of applied revisions nothing here is known to be wrong:
    two branches forking from the same head produce this shape legitimately.
    Reporting a non-zero status would fail that ordinary workflow.
    """
    if not displaced:
        print("No hybrid migrations are chained ahead of earlier revisions.")  # noqa: T201
        return 0

    for revision in displaced:
        following = ", ".join(revision.displaced)
        print(  # noqa: T201
            f"{revision.hybrid}: hardcodes down_revision={revision.target!r}; "
            f"{len(revision.displaced)} later revision(s) now chain after it: "
            f"{following}\n"
            f"  If any of those is already applied on a database, "
            f"{revision.hybrid} sits behind that database's head and will not "
            f"run there. Git history alone cannot tell: two branches forking "
            f"from the same head produce this shape legitimately. Pass "
            f"--applied to decide.",
        )
    return 0


def _report_confirmed(displaced: list[DisplacedRevision], applied: set[str]) -> int:
    """Report hybrids that cannot run on the described database.

    Exits non-zero, because every finding here is backed by a revision the
    database has actually applied.
    """
    if not displaced:
        print("No migration is chained behind an applied revision.")  # noqa: T201
        return 0

    for revision in displaced:
        blocking = ", ".join(r for r in revision.displaced if r in applied)
        print(  # noqa: T201
            f"{revision.hybrid}: hardcodes down_revision={revision.target!r}, "
            f"but {blocking} is already applied and now chains after it.\n"
            f"  {revision.hybrid} sits behind this database's head, so "
            f"'alembic upgrade head' will never run it.",
            file=sys.stderr,
        )
    return 1


def _cli() -> None:
    """CLI entry point: generate revision_chain.json, or check the chain."""
    parser = argparse.ArgumentParser(
        prog="alembic-git-revisions",
        description=(
            "Generate revision_chain.json from git history, or with --check "
            "report hybrid migrations chained ahead of earlier revisions."
        ),
    )
    parser.add_argument(
        "versions_dir",
        metavar="versions-directory",
        type=pathlib.Path,
        help="Alembic versions directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Report hybrid migrations chained ahead of revisions added "
            "before them, instead of generating the chain file. Exits 0: "
            "git history alone cannot prove a finding is a real problem."
        ),
    )
    parser.add_argument(
        "--applied",
        metavar="FILE",
        help=(
            "With --check, a file of applied revision ids (one per line, "
            "'-' for stdin) as recorded by the target database. Narrows the "
            "report to migrations that provably cannot run, and exits "
            "non-zero if there are any."
        ),
    )
    args = parser.parse_args()

    if args.applied is not None and not args.check:
        parser.error("--applied requires --check")

    # Both paths fail the same way when git is missing or shallow, and an
    # unreadable --applied file is an ordinary user error. Report all of
    # them as a message rather than a traceback.
    try:
        if args.check:
            applied = _read_applied(args.applied) if args.applied is not None else None
            displaced = find_displaced_revisions(args.versions_dir, applied=applied)
            if applied is None:
                sys.exit(_report_candidates(displaced))
            sys.exit(_report_confirmed(displaced, applied))

        generate_chain_file(args.versions_dir)
    except (RuntimeError, OSError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
