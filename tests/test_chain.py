from __future__ import annotations

import json
import pathlib
import subprocess
from unittest import mock

import pytest

from alembic_git_revisions import _chain


def _walk_chain(full: dict[str, str | None]) -> list[str]:
    """Walk a ``{revision: down_revision}`` map from root to head.

    Fails loudly on forks (two revisions sharing a down_revision) and
    on cycles (more steps than revisions).
    """
    children: dict[str | None, str] = {}
    for child_rev, parent_rev in full.items():
        if parent_rev in children:
            pytest.fail(
                f"Fork: {children[parent_rev]!r} and {child_rev!r} "
                f"both point to {parent_rev!r}",
            )
        children[parent_rev] = child_rev
    rev = children[None]
    walked = [rev]
    for _ in range(len(full)):
        if rev not in children:
            break
        rev = children[rev]
        walked.append(rev)
    else:
        pytest.fail("Cycle detected in revision chain")
    return walked


def test_chain_actual_head(tmp_path: pathlib.Path) -> None:
    """get_down_revision must return the actual chain head, not git-order head."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    # Actual chain: aaaa -> bbbb -> cccc  (cccc is the head)
    # Git order:    aaaa(0), cccc(1), bbbb(2)  (bbbb is last in git)
    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_second.py").write_text(
        'revision = "bbbb"\ndown_revision = "aaaa"\n',
    )
    (versions_dir / "cccc_third.py").write_text(
        'revision = "cccc"\ndown_revision = "bbbb"\n',
    )
    (versions_dir / "dddd_new.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "dddd"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    git_order = [
        "aaaa_root.py",
        "cccc_third.py",
        "bbbb_second.py",
        "dddd_new.py",
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    assert chain == {"dddd": "cccc"}


def test_no_multiple_heads(tmp_path: pathlib.Path) -> None:
    """get_down_revision must not create multiple heads."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_second.py").write_text(
        'revision = "bbbb"\ndown_revision = "aaaa"\n',
    )
    (versions_dir / "cccc_third.py").write_text(
        'revision = "cccc"\ndown_revision = "bbbb"\n',
    )
    (versions_dir / "dddd_new.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "dddd"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    git_order = [
        "aaaa_root.py",
        "cccc_third.py",
        "bbbb_second.py",
        "dddd_new.py",
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    assert chain == {"dddd": "cccc"}

    full_chain = {"bbbb": "aaaa", "cccc": "bbbb", **chain}
    all_revs = {"aaaa", *full_chain.keys()}
    heads = all_revs - set(full_chain.values())
    assert heads == {"dddd"}


def test_git_order_matches_chain(tmp_path: pathlib.Path) -> None:
    """Happy path: git order matches actual chain."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_second.py").write_text(
        'revision = "bbbb"\ndown_revision = "aaaa"\n',
    )
    (versions_dir / "cccc_third.py").write_text(
        'revision = "cccc"\ndown_revision = "bbbb"\n',
    )
    (versions_dir / "dddd_new.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "dddd"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    git_order = [
        "aaaa_root.py",
        "bbbb_second.py",
        "cccc_third.py",
        "dddd_new.py",
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    assert chain == {"dddd": "cccc"}


def test_multiple_dynamic_migrations(tmp_path: pathlib.Path) -> None:
    """Multiple dynamic migrations must chain in git order after the head."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_second.py").write_text(
        'revision = "bbbb"\ndown_revision = "aaaa"\n',
    )
    (versions_dir / "cccc_new1.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "cccc"\n'
        "down_revision = get_down_revision(revision)\n",
    )
    (versions_dir / "dddd_new2.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "dddd"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    git_order = [
        "aaaa_root.py",
        "bbbb_second.py",
        "cccc_new1.py",
        "dddd_new2.py",
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    assert chain == {"cccc": "bbbb", "dddd": "cccc"}


def test_uncommitted_file(tmp_path: pathlib.Path) -> None:
    """A new migration not yet in git should still find the correct head."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_second.py").write_text(
        'revision = "bbbb"\ndown_revision = "aaaa"\n',
    )
    (versions_dir / "cccc_new.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "cccc"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    # cccc_new.py is NOT in git order (uncommitted)
    git_order = [
        "aaaa_root.py",
        "bbbb_second.py",
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    assert chain == {"cccc": "bbbb"}


def test_chain_file_fallback(tmp_path: pathlib.Path) -> None:
    """When revision_chain.json exists (Docker), it is used directly."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    chain_file = tmp_path / "revision_chain.json"

    chain_file.write_text(json.dumps({"cccc": "bbbb", "bbbb": "aaaa"}))

    _chain.build_chain.cache_clear()
    try:
        result = _chain.build_chain(versions_dir)
    finally:
        _chain.build_chain.cache_clear()

    assert result == {"cccc": "bbbb", "bbbb": "aaaa"}


def test_get_down_revision_from_chain_file(tmp_path: pathlib.Path) -> None:
    """get_down_revision works with an explicit versions_dir."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    chain_file = tmp_path / "revision_chain.json"

    chain_file.write_text(json.dumps({"cccc": "bbbb"}))

    _chain.build_chain.cache_clear()
    try:
        result = _chain.get_down_revision("cccc", versions_dir=versions_dir)
    finally:
        _chain.build_chain.cache_clear()

    assert result == "bbbb"


def test_get_down_revision_missing_revision(tmp_path: pathlib.Path) -> None:
    """get_down_revision raises ValueError for unknown revisions."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    chain_file = tmp_path / "revision_chain.json"

    chain_file.write_text(json.dumps({"cccc": "bbbb"}))

    _chain.build_chain.cache_clear()
    try:
        with pytest.raises(ValueError, match="not found in migration chain"):
            _chain.get_down_revision("xxxx", versions_dir=versions_dir)
    finally:
        _chain.build_chain.cache_clear()


def test_static_file_pointing_to_dynamic(tmp_path: pathlib.Path) -> None:
    """Hybrid: static file whose down_revision points to a dynamic migration.

    Subsequent dynamic migrations must chain after the hybrid, not after
    the dynamic migration it points to.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_second.py").write_text(
        'revision = "bbbb"\ndown_revision = "aaaa"\n',
    )
    (versions_dir / "cccc_dynamic.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "cccc"\n'
        "down_revision = get_down_revision(revision)\n",
    )
    # Static file pointing to dynamic migration
    (versions_dir / "dddd_manual.py").write_text(
        'revision = "dddd"\ndown_revision = "cccc"\n',
    )
    # Another dynamic migration — must chain after dddd, not cccc
    (versions_dir / "eeee_dynamic2.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "eeee"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    git_order = [
        "aaaa_root.py",
        "bbbb_second.py",
        "cccc_dynamic.py",
        "dddd_manual.py",
        "eeee_dynamic2.py",
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    assert chain == {"cccc": "bbbb", "eeee": "dddd"}


def test_generate_chain_file(tmp_path: pathlib.Path) -> None:
    """generate_chain_file writes correct JSON."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    (versions_dir / "bbbb_dynamic.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "bbbb"\n'
        "down_revision = get_down_revision(revision)\n",
    )

    git_order = ["aaaa_root.py", "bbbb_dynamic.py"]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        _chain.generate_chain_file(versions_dir)

    chain_file = tmp_path / "revision_chain.json"
    assert chain_file.exists()
    assert json.loads(chain_file.read_text()) == {"bbbb": "aaaa"}


def test_no_git_no_chain_file(tmp_path: pathlib.Path) -> None:
    """RuntimeError when neither git nor chain file is available."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    _chain.build_chain.cache_clear()
    try:
        with (
            mock.patch.object(
                _chain,
                "_get_git_commit_order",
                return_value=None,
            ),
            pytest.raises(RuntimeError, match="No git repository found"),
        ):
            _chain.build_chain(versions_dir)
    finally:
        _chain.build_chain.cache_clear()


def test_git_commit_order_with_relative_nested_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_git_commit_order must work with multi-component relative paths.

    Regression test: when versions_dir is a relative path like
    ``a/b/versions``, git runs with cwd=``a/b`` and the pathspec must
    be just ``versions/``, not ``a/b/versions/`` (which would double
    the path and match nothing).
    """
    # Create a git repo with a nested versions directory
    repo = tmp_path / "repo"
    nested = repo / "a" / "b" / "versions"
    nested.mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Add files in separate commits to establish git order
    (nested / "aaaa_first.py").write_text("down_revision = None\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "first"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    (nested / "bbbb_second.py").write_text('down_revision = "aaaa"\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "second"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Test with a multi-component relative path (the bug scenario)
    monkeypatch.chdir(repo)
    relative_versions = pathlib.Path("a") / "b" / "versions"
    result = _chain._get_git_commit_order(relative_versions)

    assert result is not None
    assert "aaaa_first.py" in result
    assert "bbbb_second.py" in result
    assert result.index("aaaa_first.py") < result.index("bbbb_second.py")


def test_dynamic_inserted_before_hybrid_no_multiple_heads(
    tmp_path: pathlib.Path,
) -> None:
    """A dynamic migration added before a hybrid must not create multiple heads.

    Reproduces a production failure: two branches fork from the same dynamic
    head.  Branch A adds a dynamic migration; branch B adds a static
    migration (hybrid) pointing to the same head.  Branch A merges first,
    so the new dynamic migration appears *before* the hybrid in git order.

    Without a fix, both the new dynamic migration and the hybrid point to
    the same predecessor, creating a fork and ``MultipleHeads``.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    # Static root
    (versions_dir / "aaaa_root.py").write_text(
        'revision = "aaaa"\ndown_revision = None\n',
    )
    # Dynamic migration (the previous head before the two branches)
    (versions_dir / "bbbb_dynamic.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "bbbb"\n'
        "down_revision = get_down_revision(revision)\n",
    )
    # Dynamic migration added on branch A (merged first)
    (versions_dir / "cccc_dynamic.py").write_text(
        "from alembic_git_revisions import get_down_revision\n"
        'revision = "cccc"\n'
        "down_revision = get_down_revision(revision)\n",
    )
    # Hybrid: static migration added on branch B, hardcoded to the old head.
    # Merged *after* branch A, so it appears last in git order.
    (versions_dir / "dddd_hybrid.py").write_text(
        'revision = "dddd"\ndown_revision = "bbbb"\n',
    )

    git_order = [
        "aaaa_root.py",
        "bbbb_dynamic.py",
        "cccc_dynamic.py",  # branch A merged first
        "dddd_hybrid.py",  # branch B merged second
    ]

    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        chain = _chain._build_chain_from_git(versions_dir)

    # dddd hardcodes down_revision="bbbb", so it doesn't need a chain entry.
    # cccc must chain AFTER dddd (not to bbbb) to avoid multiple heads.
    # Correct chain: aaaa -> bbbb -> dddd(hybrid) -> cccc
    assert chain == {"bbbb": "aaaa", "cccc": "dddd"}

    # Reconstruct the full chain (static from files + dynamic from chain)
    # and walk it from root to head to verify the exact linear order.
    files = _chain._parse_migration_files(versions_dir, git_order)
    full = {f.revision: f.static_down_revision for f in files if not f.is_dynamic}
    full.update(chain)
    assert _walk_chain(full) == ["aaaa", "bbbb", "dddd", "cccc"]


def test_auto_discover_versions_dir(tmp_path: pathlib.Path) -> None:
    """get_down_revision auto-discovers versions_dir from caller's location."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    chain_file = tmp_path / "revision_chain.json"
    chain_file.write_text(json.dumps({"cccc": "bbbb"}))

    # Simulate a migration file calling get_down_revision
    # by mocking _discover_versions_dir to return our test dir
    _chain.build_chain.cache_clear()
    try:
        with mock.patch.object(
            _chain,
            "_discover_versions_dir",
            return_value=versions_dir,
        ):
            result = _chain.get_down_revision("cccc")
    finally:
        _chain.build_chain.cache_clear()

    assert result == "bbbb"
