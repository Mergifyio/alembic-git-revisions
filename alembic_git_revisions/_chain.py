from __future__ import annotations

import ast
import dataclasses
import functools
import inspect
import json
import pathlib
import re
import subprocess

_REVISION_FROM_FILENAME_RE = re.compile(r"^([a-f0-9]+)_")

CHAIN_FILENAME = "revision_chain.json"


@dataclasses.dataclass(frozen=True)
class MigrationFile:
    """A parsed migration file with its classification.

    Migrations come in three flavours:

    * **dynamic** — uses ``get_down_revision()`` so its predecessor is
      determined at runtime from git history.
    * **static** — has a hardcoded ``down_revision`` and manages its own
      chain.  A merge migration (``down_revision`` is a tuple/list of
      several parents) is a static migration with more than one parent.
    * **hybrid** — a static migration whose ``down_revision`` points to a
      *dynamic* revision.  It already has a hardcoded predecessor but
      must participate in the dynamic ordering so that subsequent dynamic
      files chain after it (not after the dynamic revision it points to).

    ``git_sequence`` is a position within one particular parse, not a
    stable property of the file: it indexes the ``git_order`` list that
    produced this instance.  Every file missing from that list (typically
    because it is not committed yet) receives the same end-of-list
    sentinel, so those files tie and are separated only by sorting on
    ``filename`` as well.  Construct instances through
    :func:`parse_versions_dir` rather than calling ``from_file``
    directly, which would require inventing a value for it.
    """

    revision: str
    filename: str
    git_sequence: int
    is_dynamic: bool
    # Hardcoded parent(s) for static/hybrid files; empty for dynamic files
    # and for the static root.  A tuple with >1 entry is a merge migration.
    static_down_revisions: tuple[str, ...]

    @classmethod
    def from_file(
        cls,
        path: pathlib.Path,
        git_sequence: int,
    ) -> MigrationFile:
        """Parse a migration file and classify it.

        ``revision`` and ``down_revision`` are read from the module's
        top-level assignments — the same attributes Alembic itself loads —
        rather than inferred from the filename.  This keeps the chain
        correct for any Alembic ``file_template`` (e.g. a ``date`` prefix)
        and any ``rev_id`` format (revision IDs need not be lowercase hex),
        and it tolerates type-annotated assignments such as
        ``down_revision: Union[str, None] = get_down_revision(revision)``
        that recent ``alembic init`` templates generate.
        """
        content = path.read_text(encoding="utf-8")
        fname = path.name
        try:
            assignments = _module_level_assignments(content)
        except SyntaxError as exc:
            msg = f"Cannot parse migration file {path}: {exc}"
            raise ValueError(msg) from exc

        revision = _string_value(assignments.get("revision"))
        if revision is None:
            # Fall back to the filename for files without a literal
            # ``revision`` assignment, preserving older behaviour.
            revision = _extract_revision(fname)

        down_revision = assignments.get("down_revision")
        if _is_get_down_revision_call(down_revision):
            return cls(
                revision=revision,
                filename=fname,
                git_sequence=git_sequence,
                is_dynamic=True,
                static_down_revisions=(),
            )

        return cls(
            revision=revision,
            filename=fname,
            git_sequence=git_sequence,
            is_dynamic=False,
            static_down_revisions=_down_revision_values(down_revision),
        )


def _module_level_assignments(content: str) -> dict[str, ast.expr]:
    """Map each module-level assigned name to its value node.

    Handles both plain (``x = ...``) and annotated (``x: T = ...``)
    assignments and ignores anything nested inside a function or class,
    mirroring how Alembic reads a migration module's attributes.
    """
    values: dict[str, ast.expr] = {}
    for node in ast.parse(content).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            values[node.target.id] = node.value
    return values


def _string_value(node: ast.expr | None) -> str | None:
    """Return the value of a string-literal node, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _down_revision_values(node: ast.expr | None) -> tuple[str, ...]:
    """Return the hardcoded parent revisions declared by ``down_revision``.

    Handles a single revision (``"abc"``), a merge migration whose
    ``down_revision`` is a tuple or list of parents (``("abc", "def")``)
    and ``None``/absent.  Returns an empty tuple when there is no
    hardcoded parent.
    """
    single = _string_value(node)
    if single is not None:
        return (single,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(
            value
            for element in node.elts
            if (value := _string_value(element)) is not None
        )
    return ()


def _is_get_down_revision_call(node: ast.expr | None) -> bool:
    """Return whether *node* is a call to ``get_down_revision(...)``.

    Matches both the bare ``get_down_revision(...)`` and an attribute form
    such as ``agr.get_down_revision(...)``.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "get_down_revision"
    return isinstance(func, ast.Attribute) and func.attr == "get_down_revision"


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

    Uses ``git log --reverse --topo-order --diff-filter=A --no-renames`` to
    list files in the order they were first added, walking the commit graph
    in topological order from oldest to newest.  The walk enters merges (see
    ``--topo-order`` below), so the history it traverses need not be linear;
    what topological order guarantees is that no commit is listed before an
    ancestor it depends on.

    The command runs without a pathspec so that it scans the full repo
    history.  This is necessary to preserve chronological ordering when
    migrations are moved to a new directory: the original add commits
    (in the old directory) come before the move commit, so each file's
    first appearance in the log reflects its true creation order.  Results
    are filtered against the set of ``.py`` files that currently exist in
    *versions_dir*, so unrelated files are excluded.  Deduplication
    (keep-first) ensures that a file moved across directories keeps the
    ordering of its original add, not the later move.

    ``--topo-order`` makes the sequence a property of the commit graph
    rather than of commit timestamps.  Without it, a migration whose
    original feature-branch commit is reachable from HEAD would receive
    that early commit's date as its sequence — even when another
    migration was merged onto main earlier — breaking the append-only
    chain order. (INC-1342.)

    ``--first-parent`` also fixes that, but only for repositories that
    merge feature branches into the integration branch.  It is wrong for
    the inverse layout, where features land by rebase-and-merge (leaving
    the integration branch linear) and a merge commit is used only to
    promote a release: on the release branch a whole release's migrations
    arrive in a single merge, whose diff git lists alphabetically, so the
    add order is lost and the two branches derive different chains.
    ``--topo-order`` is correct for both.

    ``--no-renames`` is critical: without it, git's rename detection can
    cause a renamed migration file (e.g. when changing its revision ID)
    to be treated as a rename rather than an add.  ``--diff-filter=A``
    then silently excludes the file, which causes it to receive the
    fallback ``uncommitted_seq`` ordering — placing it at the tail of
    the chain regardless of its actual position in git history.

    Returns an ordered list of filenames (deduplicated, oldest first),
    or ``None`` if git is not available or the repository is a shallow clone
    (which produces incorrect results).
    """
    if _is_shallow_clone(versions_dir):
        return None

    existing = {f.name for f in versions_dir.glob("*.py")}

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--reverse",
                "--diff-filter=A",
                "--no-renames",
                "--topo-order",
                "--format=",
                "--name-only",
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
        if fname in existing and fname not in order:
            order.append(fname)

    return order


def _extract_revision(filename: str) -> str:
    """Extract revision ID from filename like '5c9eb899ede0_slug.py'."""
    m = _REVISION_FROM_FILENAME_RE.match(filename)
    if not m:
        msg = f"Cannot extract revision from filename: {filename!r}"
        raise ValueError(msg)
    return m.group(1)


def parse_versions_dir(versions_dir: pathlib.Path) -> list[MigrationFile]:
    """Parse and classify every migration in *versions_dir*, in git add order.

    Exposes the same view the chain builder works from, for callers that
    want to inspect classification or ordering without building a chain.

    The order is the raw order files were added to git, sorted by
    ``(git_sequence, filename)``.  It is **not** the order the resulting
    chain walks: :func:`build_chain` re-parents a hybrid to sit
    immediately after the revision it hardcodes, which can move it far
    from its own add position.  Use :func:`build_chain` when the question
    is what Alembic will traverse.

    Unlike :func:`build_chain` this never falls back to a generated chain
    file, because that file records only ``{revision: down_revision}``;
    it carries neither the classification nor the ordering this returns,
    so there is nothing to reconstruct from.  Git is therefore required,
    and its absence raises rather than yielding a plausible wrong order.

    Results are not cached, so each call re-reads git and re-parses every
    file and will pick up edits made since the last call.
    """
    git_order = _get_git_commit_order(versions_dir)
    if git_order is None:
        msg = (
            f"Cannot read git history for {versions_dir}: git is not available "
            f"or this is a shallow clone."
        )
        raise RuntimeError(msg)
    files = _parse_migration_files(versions_dir, git_order)
    return sorted(files, key=lambda f: (f.git_sequence, f.filename))


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

    Only considers static files whose parents stay within the static set.
    Hybrid files (static files pointing to a dynamic revision) are
    excluded — they extend the dynamic chain, not the static one.  Merge
    migrations (more than one parent) are handled: every parent is treated
    as consumed, so the branches they join do not look like extra heads.
    """
    static_revisions = {f.revision for f in files if not f.is_dynamic}

    pure_static = {
        f.revision
        for f in files
        if not f.is_dynamic
        and all(parent in static_revisions for parent in f.static_down_revisions)
    }
    consumed = {
        parent
        for f in files
        if f.revision in pure_static
        for parent in f.static_down_revisions
    }
    heads = pure_static - consumed

    return heads.pop() if len(heads) == 1 else None


def _build_dynamic_chain(
    files: list[MigrationFile],
    static_head: str | None,
) -> dict[str, str]:
    """Build ``{revision: down_revision}`` for dynamic migrations.

    Dynamic files are sorted by ``(git_sequence, filename)`` and chained
    linearly after *static_head*.  Hybrid files (static files whose
    ``down_revision`` points to a dynamic revision) are placed
    immediately after their target so that dynamic migrations added by
    concurrent branches chain after the hybrid, not to the same target
    (which would create a fork / multiple heads).

    Hybrids don't get entries in the returned dict since they already
    have a hardcoded ``down_revision``.
    """
    dynamic_revisions = {f.revision for f in files if f.is_dynamic}

    def _dynamic_parent(f: MigrationFile) -> str | None:
        """The dynamic revision a hybrid static file points to, if any."""
        if f.is_dynamic:
            return None
        return next(
            (
                parent
                for parent in f.static_down_revisions
                if parent in dynamic_revisions
            ),
            None,
        )

    dynamic_participants: list[MigrationFile] = [
        f for f in files if f.is_dynamic or _dynamic_parent(f) is not None
    ]

    # O(1) lookup for each participant's git_sequence by revision.
    seq_by_rev = {f.revision: f.git_sequence for f in dynamic_participants}

    # Map each hybrid's target revision to that target's git_sequence so
    # the hybrid sorts right after its target (not at its own commit time).
    target_seq: dict[str, int] = {}
    for f in dynamic_participants:
        target = _dynamic_parent(f)
        if target is not None:
            target_seq[target] = seq_by_rev.get(target, f.git_sequence)

    def _sort_key(f: MigrationFile) -> tuple[int, int, str]:
        target = _dynamic_parent(f)
        if target is not None:
            # Place hybrid right after its target (the 1 ensures it
            # sorts after the target itself at the same git_sequence).
            return (target_seq[target], 1, f.filename)
        return (f.git_sequence, 0, f.filename)

    dynamic_participants.sort(key=_sort_key)

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
    chain_file = versions_dir.parent / CHAIN_FILENAME
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
    chain_file = versions_dir.parent / CHAIN_FILENAME
    with chain_file.open("w", encoding="utf-8") as f:
        json.dump(chain, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Generated {chain_file} with {len(chain)} revisions")  # noqa: T201
