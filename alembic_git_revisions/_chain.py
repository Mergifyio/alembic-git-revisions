from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import pathlib
import re
import subprocess

_DOWN_REVISION_RE = re.compile(r'^down_revision\s*=\s*"([^"]+)"', re.MULTILINE)
_DYNAMIC_DOWN_REVISION_RE = re.compile(
    r"^down_revision\s*=\s*get_down_revision\(",
    re.MULTILINE,
)
_REVISION_FROM_FILENAME_RE = re.compile(r"^([a-f0-9]+)_")

_CHAIN_FILENAME = "revision_chain.json"


@dataclasses.dataclass(frozen=True)
class MigrationFile:
    """A parsed migration file with its classification.

    Migrations come in three flavours:

    * **dynamic** — uses ``get_down_revision()`` so its predecessor is
      determined at runtime from git history.
    * **static** — has a hardcoded ``down_revision`` string and manages
      its own chain.
    * **hybrid** — a static migration whose ``down_revision`` points to a
      *dynamic* revision.  It already has a hardcoded predecessor but
      must participate in the dynamic ordering so that subsequent dynamic
      files chain after it (not after the dynamic revision it points to).
    """

    revision: str
    filename: str
    git_sequence: int
    is_dynamic: bool
    static_down_revision: str | None  # set only for static/hybrid files

    @classmethod
    def from_file(
        cls,
        path: pathlib.Path,
        git_sequence: int,
    ) -> MigrationFile:
        """Parse a migration file and classify it."""
        content = path.read_text(encoding="utf-8")
        fname = path.name
        revision = _extract_revision(fname)

        if _DYNAMIC_DOWN_REVISION_RE.search(content):
            return cls(
                revision=revision,
                filename=fname,
                git_sequence=git_sequence,
                is_dynamic=True,
                static_down_revision=None,
            )

        m = _DOWN_REVISION_RE.search(content)
        return cls(
            revision=revision,
            filename=fname,
            git_sequence=git_sequence,
            is_dynamic=False,
            static_down_revision=m.group(1) if m else None,
        )


def _discover_versions_dir() -> pathlib.Path:
    """Auto-discover the versions directory from the calling migration file.

    Walks the call stack to find the first caller outside this package.
    That caller is expected to be a migration file living in a ``versions/``
    directory.
    """
    this_pkg = pathlib.Path(__file__).parent

    for frame_info in inspect.stack():
        caller_path = pathlib.Path(frame_info.filename).resolve()
        # Skip frames from this package
        try:
            caller_path.relative_to(this_pkg)
        except ValueError:
            # Outside this package — this is the migration file
            return caller_path.parent

    msg = (
        "Cannot auto-discover versions directory: "
        "no caller outside alembic_git_revisions found in the call stack."
    )
    raise RuntimeError(msg)


def _is_shallow_clone(versions_dir: pathlib.Path) -> bool:
    """Return True if the repository is a shallow clone."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            capture_output=True,
            text=True,
            check=True,
            cwd=versions_dir.parent,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return result.stdout.strip() == "true"


def _get_git_commit_order(versions_dir: pathlib.Path) -> list[str] | None:
    """Get the commit order for migration files in *versions_dir*.

    Uses ``git log --reverse --diff-filter=A`` to list files in the order
    they were first added, walking the linear commit tree from oldest to
    newest.

    Returns an ordered list of filenames (deduplicated, oldest first),
    or ``None`` if git is not available or the repository is a shallow clone
    (which produces incorrect results).
    """
    if _is_shallow_clone(versions_dir):
        return None

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--reverse",
                "--diff-filter=A",
                "--format=",
                "--name-only",
                "--",
                versions_dir.name + "/",
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=versions_dir.parent,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    order: list[str] = []

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or not line.endswith(".py"):
            continue
        fname = pathlib.Path(line).name
        if fname not in order:
            order.append(fname)

    return order


def _extract_revision(filename: str) -> str:
    """Extract revision ID from filename like '5c9eb899ede0_slug.py'."""
    m = _REVISION_FROM_FILENAME_RE.match(filename)
    if not m:
        msg = f"Cannot extract revision from filename: {filename!r}"
        raise ValueError(msg)
    return m.group(1)


def _parse_migration_files(
    versions_dir: pathlib.Path,
    git_order: list[str],
) -> list[MigrationFile]:
    """Read all migration files and classify them as dynamic or static."""
    sequence_by_name = {fname: i for i, fname in enumerate(git_order)}
    uncommitted_seq = len(git_order)

    return [
        MigrationFile.from_file(
            py_file,
            sequence_by_name.get(py_file.name, uncommitted_seq),
        )
        for py_file in versions_dir.glob("*.py")
    ]


def _find_static_head(files: list[MigrationFile]) -> str | None:
    """Find the single head of the purely-static migration chain.

    Only considers static files whose ``down_revision`` stays within
    the static set.  Hybrid files (static files pointing to a dynamic
    revision) are excluded — they extend the dynamic chain, not the
    static one.
    """
    static_chain = {
        f.revision: f.static_down_revision for f in files if not f.is_dynamic
    }

    pure_static = {
        rev
        for rev, down_rev in static_chain.items()
        if down_rev is None or down_rev in static_chain
    }
    pure_static_down = {
        static_chain[rev] for rev in pure_static if static_chain[rev] is not None
    }
    heads = pure_static - pure_static_down

    return heads.pop() if len(heads) == 1 else None


def _build_dynamic_chain(
    files: list[MigrationFile],
    static_head: str | None,
) -> dict[str, str]:
    """Build ``{revision: down_revision}`` for dynamic migrations.

    Dynamic files are sorted by ``(git_sequence, filename)`` and chained
    linearly after *static_head*.

    Hybrid files (static files whose ``down_revision`` points to a
    dynamic revision) participate in the ordering so that subsequent
    dynamic files chain after them, but they don't get entries in the
    returned dict since they already have a hardcoded ``down_revision``.
    """
    dynamic_revisions = {f.revision for f in files if f.is_dynamic}

    dynamic_participants: list[MigrationFile] = [
        f for f in files if f.is_dynamic or f.static_down_revision in dynamic_revisions
    ]

    dynamic_participants.sort(key=lambda f: (f.git_sequence, f.filename))

    chain: dict[str, str] = {}
    prev_revision = static_head

    for f in dynamic_participants:
        if prev_revision is not None and f.is_dynamic:
            chain[f.revision] = prev_revision
        prev_revision = f.revision

    return chain


def _build_chain_from_git(
    versions_dir: pathlib.Path,
) -> dict[str, str] | None:
    """Build ``{revision: down_revision}`` for dynamic migrations only.

    Returns ``None`` if git is not available.
    """
    git_order = _get_git_commit_order(versions_dir)
    if git_order is None:
        return None

    files = _parse_migration_files(versions_dir, git_order)
    static_head = _find_static_head(files)
    return _build_dynamic_chain(files, static_head)


def _load_chain_from_file(chain_file: pathlib.Path) -> dict[str, str]:
    """Load the pre-generated revision chain from JSON."""
    with chain_file.open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@functools.cache
def build_chain(versions_dir: pathlib.Path) -> dict[str, str]:
    """Build the revision chain for *versions_dir*.

    Prefers a pre-generated ``revision_chain.json`` file (sibling of the
    versions directory) over git.  The JSON file is the preferred source
    because git requires full history (shallow clones produce incorrect
    results).  The file is generated in CI and Docker builds from a full
    clone, so it is always correct when present.  In local development
    the file is typically absent so git is used.

    The result is cached per *versions_dir*.  Use
    ``build_chain.cache_clear()`` to reset (e.g. in tests).
    """
    chain_file = versions_dir.parent / _CHAIN_FILENAME
    if chain_file.exists():
        return _load_chain_from_file(chain_file)
    chain = _build_chain_from_git(versions_dir)
    if chain is not None:
        return chain
    msg = (
        f"No git repository found and {chain_file} does not exist. "
        f"Run: alembic-git-revisions {versions_dir}"
    )
    raise RuntimeError(msg)


def get_down_revision(
    revision: str,
    versions_dir: pathlib.Path | None = None,
) -> str:
    """Return the down_revision for the given revision ID.

    If *versions_dir* is not provided, it is auto-discovered from the
    calling migration file's location (the file must live in the versions
    directory).
    """
    if versions_dir is None:
        versions_dir = _discover_versions_dir()

    chain = build_chain(versions_dir)
    try:
        return chain[revision]
    except KeyError:
        msg = (
            f"Revision {revision!r} not found in migration chain. "
            f"Known revisions: {sorted(chain.keys())}"
        )
        raise ValueError(msg) from None


def generate_chain_file(versions_dir: pathlib.Path) -> None:
    """Generate the revision_chain.json file from git history.

    This should be run before building Docker images or in any environment
    where git won't be available at runtime.
    """
    chain = _build_chain_from_git(versions_dir)
    if chain is None:
        msg = (
            "Cannot generate chain file: "
            "git is not available or this is a shallow clone."
        )
        raise RuntimeError(msg)
    chain_file = versions_dir.parent / _CHAIN_FILENAME
    with chain_file.open("w", encoding="utf-8") as f:
        json.dump(chain, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Generated {chain_file} with {len(chain)} revisions")  # noqa: T201
