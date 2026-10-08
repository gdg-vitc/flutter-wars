"""Guards that no credentials are committed and diagnostics never leak them."""

import pathlib
import re
import subprocess

import pytest
from sqlalchemy.engine import make_url

from app.infra.db import DatabaseTarget

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _git_available() -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# The scan reads git-tracked files; it cannot run outside a git work tree.
requires_git = pytest.mark.skipif(
    not _git_available(), reason="not a git work tree; cannot scan tracked files"
)

# A connection string that contains a real-looking password (not a placeholder).
SUSPICIOUS = re.compile(
    rb"postgres(?:ql)?(?:\+psycopg)?://[^/\s:]+:(?!PASSWORD|password|\$\{|<|\.\.\.)[^@/\s]{6,}@"
)


def _tracked_files() -> list[str]:
    """Tracked files to scan for committed credentials.

    Excludes test files and `*.example` files: tests intentionally contain
    synthetic credentials (dummy users/secrets) and examples contain placeholders,
    so neither should be reported as a leak.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    files = [line for line in out.stdout.splitlines() if line]
    return [f for f in files if "/tests/" not in f and not f.endswith(".example")]


@requires_git
def test_env_files_are_not_tracked():
    tracked = set(_tracked_files())
    offenders = [f for f in tracked if pathlib.Path(f).name in (".env", ".dev.vars")]
    assert not offenders, f"Secret files must never be tracked: {offenders}"


@requires_git
def test_no_credential_bearing_urls_in_tracked_files():
    offenders = []
    for rel in _tracked_files():
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if SUSPICIOUS.search(data):
            offenders.append(rel)
    assert not offenders, f"Possible committed credentials in: {offenders}"


def test_database_target_repr_is_redacted():
    secret = "another-secret-value"
    target = DatabaseTarget(
        url=make_url(f"postgresql+psycopg://app:{secret}@host:5432/db"),
        source="settings",
    )
    assert secret not in repr(target)
    assert "***" in repr(target)
