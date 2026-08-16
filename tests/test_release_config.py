"""Tests that the release pipeline still points at files that exist.

`deployment.yaml` was renamed to `deployment.example.yaml`, and two places referenced it
by name: the `assets` list in `.releaserc.json` and the bump list in
`scripts/update_docker_docs.py`. Neither would have failed loudly — `_update_file`
printed "not found, skipping", and a semantic-release asset that matches nothing is
ignored. The release would have kept succeeding while quietly leaving the example
pinned to an old version.

That is the same shape as every other defect found this week: the fallback works, the
signal does not. These tests make the reference itself the thing that breaks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_every_release_asset_exists():
    config = json.loads((_ROOT / ".releaserc.json").read_text(encoding="utf-8"))
    assets = next(
        plugin[1]["assets"]
        for plugin in config["plugins"]
        if isinstance(plugin, list) and plugin[0] == "@semantic-release/git"
    )
    missing = [asset for asset in assets if not (_ROOT / asset).exists()]
    assert missing == [], f"release assets that do not exist: {missing}"


def test_the_asset_list_is_not_empty():
    """Guards the guard: a parsing slip would make the check above vacuous."""
    config = json.loads((_ROOT / ".releaserc.json").read_text(encoding="utf-8"))
    assets = next(
        plugin[1]["assets"]
        for plugin in config["plugins"]
        if isinstance(plugin, list) and plugin[0] == "@semantic-release/git"
    )
    assert len(assets) >= 3


def test_every_file_the_bump_script_touches_exists():
    source = (_ROOT / "scripts" / "update_docker_docs.py").read_text(encoding="utf-8")
    paths = re.findall(r'Path\("([^"]+)"\)', source)
    assert paths, "no Path(...) found — this test's parser broke"
    missing = [p for p in paths if not (_ROOT / p).exists()]
    assert missing == [], f"the release bumps files that do not exist: {missing}"


def test_the_example_manifest_carries_a_pinned_tag_the_bump_can_match():
    """The regex only rewrites `:latest` or `:vX.Y.Z`; anything else is a silent no-op."""
    content = (_ROOT / "deployment.example.yaml").read_text(encoding="utf-8")
    assert re.search(
        r"image:\s*ghcr\.io/[^/]+/service-checker:(?:latest|v\d+\.\d+\.\d+)", content
    )
