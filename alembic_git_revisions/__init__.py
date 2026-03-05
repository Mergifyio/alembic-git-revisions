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

import json
import pathlib

import click

from alembic_git_revisions._chain import (
    ChainNotAppendOnlyError as ChainNotAppendOnlyError,
)
from alembic_git_revisions._chain import (
    build_chain as build_chain,
)
from alembic_git_revisions._chain import (
    generate_chain_file as generate_chain_file,
)
from alembic_git_revisions._chain import (
    get_down_revision as get_down_revision,
)
from alembic_git_revisions._chain import (
    verify_append_only as verify_append_only,
)


@click.command()
@click.argument(
    "versions_directory",
    type=click.Path(exists=True, file_okay=False, path_type=pathlib.Path),
)
@click.option(
    "--previous-chain-path",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    default=None,
    help=(
        "Path to a previously generated revision_chain.json. "
        "The new chain is compared against it and the command "
        "fails if any entries were removed or modified."
    ),
)
def _cli(
    versions_directory: pathlib.Path,
    previous_chain_path: pathlib.Path | None,
) -> None:
    """Generate revision_chain.json from git history."""
    generate_chain_file(versions_directory)

    if previous_chain_path is not None:
        chain_file = versions_directory.parent / "revision_chain.json"
        with previous_chain_path.open(encoding="utf-8") as f:
            previous_chain: dict[str, str] = json.load(f)
        with chain_file.open(encoding="utf-8") as f:
            new_chain: dict[str, str] = json.load(f)
        try:
            verify_append_only(previous_chain, new_chain)
        except ChainNotAppendOnlyError as exc:
            for err in exc.errors:
                click.echo(f"  {err}", err=True)
            msg = "revision chain is not append-only"
            raise click.ClickException(msg) from None
        added = len(new_chain) - len(previous_chain)
        click.echo(f"Append-only check passed ({added} new entries)")
