#!/usr/bin/env python3
#
# tests/test_docs_workflow.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


def test_docs_toolchain_is_materialized_from_pyproject() -> None:
    pyproject_path = Path("pyproject.toml")
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)

    docs_dependencies = pyproject["project"]["optional-dependencies"]["docs"]
    assert "mkdocs==1.6.1" in docs_dependencies
    assert "mkdocs-material==9.7.7" in docs_dependencies

    result = subprocess.run(
        [sys.executable, "tools/pyproject-deps.py", "docs"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == docs_dependencies
    assert not Path("docs/requirements-docs.txt").exists()

    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "requirements-docs.txt" in gitignore
    assert "docs/changelog.md" in gitignore
    assert "docs/license.md" in gitignore


def test_docs_workflow_uses_one_reproducible_build_and_root_config() -> None:
    workflow = Path(".github/workflows/docs-build.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "schedule:" in workflow
    assert "python3 tools/pyproject-deps.py docs > requirements-docs.txt" in workflow
    assert "cp CHANGELOG.md docs/changelog.md" in workflow
    assert "cp LICENSE docs/license.md" in workflow
    assert "mkdocs build --strict" in workflow
    assert "mkdocs build -f docs/mkdocs.yml --strict" not in workflow
    # No version pinning: latest is greatest, including for the scanner action.
    assert "aquasecurity/trivy-action@master" in workflow
    # lychee v0.24+ rejects root-relative links (the site lives under /caddybuddy/) without a root dir.
    assert '--root-dir \'${{ runner.temp }}/linkcheck-root\'' in workflow
    assert 'ln -sfn "$GITHUB_WORKSPACE/site" "$RUNNER_TEMP/linkcheck-root/caddybuddy"' in workflow

    config = Path("mkdocs.yml").read_text(encoding="utf-8")
    assert "docs_dir: docs" in config
    assert "site_dir: site" in config
    assert not Path("docs/mkdocs.yml").exists()
