from __future__ import annotations

import json
import pathlib
import subprocess
from unittest import mock

import pytest

from alembic_git_revisions import _chain


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
    # Walk from root (down_revision=None) to head.
    children = {v: k for k, v in full.items()}
    rev = children[None]  # root
    walked = [rev]
    while rev in children:
        rev = children[rev]
        walked.append(rev)
    assert walked == ["aaaa", "bbbb", "dddd", "cccc"]


def test_mergify_engine_architecture(tmp_path: pathlib.Path) -> None:
    """Reproduce the mergify-engine migration layout and verify the chain file.

    mergify-engine has three tiers of migrations:

    * A long static chain (394 migrations with hardcoded down_revision).
    * A dynamic chain (22 migrations using get_down_revision()).
    * One hybrid migration (static down_revision pointing to a dynamic rev).

    This test recreates that architecture at a smaller scale and verifies
    that generate_chain_file produces a JSON file that, combined with the
    static down_revisions, yields one linear chain from root to head.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    # -- Static chain: s1 → s2 → s3 (like the 394 static migrations) --
    (versions_dir / "aa01_static_root.py").write_text(
        'revision = "aa01"\ndown_revision = None\n',
    )
    (versions_dir / "aa02_static_mid.py").write_text(
        'revision = "aa02"\ndown_revision = "aa01"\n',
    )
    (versions_dir / "aa03_static_head.py").write_text(
        'revision = "aa03"\ndown_revision = "aa02"\n',
    )

    # -- Dynamic chain: d1 → d2 → d3 → d4 → d5 (like the 22 dynamic) --
    for i in range(1, 6):
        (versions_dir / f"bb0{i}_dynamic_{i}.py").write_text(
            "from alembic_git_revisions import get_down_revision\n"
            f'revision = "bb0{i}"\n'
            "down_revision = get_down_revision(revision)\n",
        )

    # -- Hybrid: static migration pointing to dynamic d4 (like 34c2e9a4b043) --
    # In production this was added on a separate branch and merged after d5.
    (versions_dir / "cc01_hybrid.py").write_text(
        'revision = "cc01"\ndown_revision = "bb04"\n',
    )

    # Git order: statics first, then dynamics, then hybrid last (as in prod).
    # d5 was merged before the hybrid, so it appears earlier in git order.
    git_order = [
        "aa01_static_root.py",
        "aa02_static_mid.py",
        "aa03_static_head.py",
        "bb01_dynamic_1.py",
        "bb02_dynamic_2.py",
        "bb03_dynamic_3.py",
        "bb04_dynamic_4.py",
        "bb05_dynamic_5.py",  # branch A merged first
        "cc01_hybrid.py",  # branch B merged second
    ]

    # Generate the chain file (the JSON produced for Docker/CI builds).
    _chain.build_chain.cache_clear()
    with mock.patch.object(
        _chain,
        "_get_git_commit_order",
        return_value=git_order,
    ):
        _chain.generate_chain_file(versions_dir)

    # Read the generated JSON.
    chain_file = tmp_path / "revision_chain.json"
    assert chain_file.exists()
    chain = json.loads(chain_file.read_text())

    # The chain file must contain exactly the dynamic migrations.
    # The hybrid (cc01) is NOT in the file — it has a hardcoded down_revision.
    assert chain == {
        "bb01": "aa03",  # first dynamic chains after static head
        "bb02": "bb01",
        "bb03": "bb02",
        "bb04": "bb03",
        # bb05 must chain after the hybrid, not after bb04 (the bug)
        "bb05": "cc01",
    }

    # Reconstruct the full chain (static + dynamic + hybrid) and walk it.
    files = _chain._parse_migration_files(versions_dir, git_order)
    full = {f.revision: f.static_down_revision for f in files if not f.is_dynamic}
    full.update(chain)

    children = {v: k for k, v in full.items()}
    rev = children[None]
    walked = [rev]
    while rev in children:
        rev = children[rev]
        walked.append(rev)

    assert walked == [
        "aa01",  # static root
        "aa02",
        "aa03",  # static head
        "bb01",  # dynamic chain starts
        "bb02",
        "bb03",
        "bb04",  # hybrid's target
        "cc01",  # hybrid (hardcoded down_revision="bb04")
        "bb05",  # last dynamic, chains after hybrid
    ]


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
