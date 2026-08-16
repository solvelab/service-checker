"""Tests for the shared Statuspage parsing.

Five modules read the same document and each carried a byte-identical copy of these
functions. The duplication was not free: a `components` that was not a list of objects
raised `AttributeError`, and the same one-line fix had to be written five times. It was
found while bug-hunting `cloudflare` and had to be filed as a defect against the other
four.

Where the exception escaped from is worse than the exception. `extract_components` runs
outside the try/except that wraps the HTTP request, so it left `check()` entirely. The
scheduler catches it — but by then the module never returned `MonitorStatus.ERROR`, so
the notification manager never saw a failed evaluation, the error streak never counted,
and the dead-monitor notification never fired. The breakage lived in the log and nowhere
else, which is exactly what that notification exists to prevent.

The contract this file pins: **never raise for a payload shape**. Unusable input comes
back empty, and the caller turns empty into `MonitorStatus.ERROR` with a readable reason.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ModuleConfig, RuleConfig
from app.core.statuspage import extract_components, slugify
from app.core.types import MonitorStatus

STATUSPAGE_MODULES = ("bitbucket", "github", "openai", "claude", "cloudflare")


def _check(slug, payload, service_filter=None):
    module = importlib.import_module(f"app.modules.{slug}.monitor")
    monitor = module.get_monitor(slug)
    monitor.configure(
        ModuleConfig(
            slug=slug,
            url=f"https://{slug}.example.com/api/v2/summary.json",
            interval_seconds=60,
            timeout_seconds=10.0,
            user_agent="test/1.0",
            rule=RuleConfig(kind="status", value="major_outage"),
            service_filter=list(service_filter or []),
            enabled=True,
        )
    )
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    return asyncio.run(monitor.check(http_client=client, logger=MagicMock(spec=logging.Logger)))


# ---------------------------------------------------------------------------
# The defect, across every module that shared the copy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug", STATUSPAGE_MODULES)
@pytest.mark.parametrize(
    "payload",
    [
        {"components": {"nope": True}},          # a dict iterates its own keys
        {"components": "nope"},                  # a string iterates its characters
        {"components": ["a", "b"]},              # a list of the wrong thing
        {"components": [["nested"]]},
        {"components": 7},        # truthy e nao-iteravel: o `for` levantaria TypeError
        {"components": [None]},
    ],
    ids=["dict", "string", "list-of-strings", "list-of-lists", "truthy-number", "list-of-none"],
)
def test_a_malformed_components_is_an_error_not_an_exception(slug, payload):
    """The exception used to leave `check()` and skip the ERROR result entirely."""
    assert _check(slug, payload).status == MonitorStatus.ERROR


@pytest.mark.parametrize("slug", STATUSPAGE_MODULES)
def test_the_error_reason_is_readable(slug):
    result = _check(slug, {"components": {"nope": True}})
    assert "no components in status response" in result.reason


@pytest.mark.parametrize("slug", STATUSPAGE_MODULES)
def test_every_statuspage_module_uses_the_shared_parser(slug):
    """A module that reintroduces its own copy reintroduces the defect with it."""
    module = importlib.import_module(f"app.modules.{slug}.monitor")
    assert module.extract_components is extract_components


# ---------------------------------------------------------------------------
# extract_components — shape by shape
# ---------------------------------------------------------------------------

def test_a_well_formed_payload_is_normalised():
    components = extract_components(
        {"components": [{"id": "abc", "name": "CDN/Cache", "status": "operational"}]}
    )
    assert components == [
        {"id": "abc", "name": "CDN/Cache", "status": "operational", "slug": "cdn-cache"}
    ]


def test_a_missing_components_key_is_empty():
    assert extract_components({"page": {}}) == []


def test_an_empty_components_list_is_empty():
    assert extract_components({"components": []}) == []


def test_the_whole_payload_not_being_a_dict_is_empty():
    for payload in (None, [], "nope", 7, 0):
        assert extract_components(payload) == []


def test_a_valid_component_survives_an_invalid_neighbour():
    """Partial garbage must not cost the components that are readable."""
    components = extract_components(
        {"components": [{"id": "a", "name": "A", "status": "operational"}, 7, None, "x"]}
    )
    assert [c["id"] for c in components] == ["a"]


def test_a_component_without_an_id_falls_back_to_its_slug():
    assert extract_components({"components": [{"name": "Git Operations"}]})[0]["id"] == (
        "git-operations"
    )


def test_a_component_without_a_name_becomes_unknown():
    assert extract_components({"components": [{"id": "x"}]})[0]["name"] == "unknown"


def test_a_component_without_a_status_becomes_unknown():
    assert extract_components({"components": [{"id": "x", "name": "A"}]})[0]["status"] == (
        "unknown"
    )


def test_a_non_string_name_does_not_crash_slugify():
    component = extract_components({"components": [{"id": "x", "name": 42}]})[0]
    assert component["name"] == "42"
    assert component["slug"] == "42"


def test_a_non_string_status_is_coerced():
    assert extract_components({"components": [{"id": "x", "name": "A", "status": 1}]})[0][
        "status"
    ] == "1"


def test_a_non_string_id_is_coerced():
    assert extract_components({"components": [{"id": 99, "name": "A"}]})[0]["id"] == "99"


def test_a_nested_dict_as_name_does_not_crash():
    component = extract_components({"components": [{"id": "x", "name": {"deep": True}}]})[0]
    assert isinstance(component["name"], str)


def test_a_thousand_components_are_all_parsed():
    payload = {"components": [{"id": f"c{i}", "name": f"C {i}"} for i in range(1000)]}
    assert len(extract_components(payload)) == 1000


def test_two_components_with_the_same_name_stay_two():
    """Distinctness: same display name, different ids, still two components."""
    components = extract_components(
        {"components": [{"id": "a", "name": "Tunnel"}, {"id": "b", "name": "Tunnel"}]}
    )
    assert [c["id"] for c in components] == ["a", "b"]


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify_collapses_punctuation_and_trims():
    assert slugify("Git via HTTPS") == "git-via-https"
    assert slugify("CDN/Cache") == "cdn-cache"
    assert slugify("  Hello World  ") == "hello-world"
    assert slugify("Bring Your Own IP (BYOIP)") == "bring-your-own-ip-byoip"


def test_slugify_of_punctuation_only_is_empty():
    assert slugify("---") == ""
    assert slugify("!!!") == ""


def test_slugify_accepts_a_non_string():
    assert slugify(42) == "42"
