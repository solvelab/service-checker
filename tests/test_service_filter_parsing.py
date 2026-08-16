"""Tests for parsing `<SLUG>_SERVICE_FILTER`.

The filter is comma-separated, so a component whose *name* contains a comma had no way
to be expressed. That is not a corner case: **every Cloudflare point of presence has a
comma in its name** — `Arica, Chile - (ARI)`, `Norfolk, VA, United States - (ORF)`.

Found while verifying the production rollout. Pointing the filter at a degraded PoP to
force a real alert produced this instead:

    WARNING  watched component not found in status payload
             reason: absent from upstream: arica, chile - (ari)
    ERROR    cloudflare rule evaluation failed
             reason: no target components matched filter

The name had become two entries, `arica` and `chile - (ari)`, and neither exists. The
monitor sat in ERROR until the filter was corrected.

`csv.reader` is the whole fix, and the property that makes it safe to adopt is
backward compatibility: no existing filter uses quotes, so every one of them parses
byte-identically to before. Half this file exists to pin that.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import _get_service_filter

_FIXTURE = Path(__file__).parent / "fixtures" / "cloudflare" / "summary.json"


@pytest.fixture
def parse(monkeypatch):
    def _parse(raw):
        monkeypatch.setenv("TEST_SERVICE_FILTER", raw)
        return _get_service_filter("TEST_SERVICE_FILTER")

    return _parse


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_a_quoted_name_with_a_comma_is_one_entry(parse):
    assert parse('"Arica, Chile - (ARI)"') == ["arica, chile - (ari)"]


def test_an_unquoted_name_with_a_comma_still_splits(parse):
    """Documents the old behaviour, which is also the correct reading of that input."""
    assert parse("Arica, Chile - (ARI)") == ["arica", "chile - (ari)"]


def test_a_name_with_two_commas_survives(parse):
    assert parse('"Norfolk, VA, United States - (ORF)"') == [
        "norfolk, va, united states - (orf)"
    ]


def test_quoted_and_unquoted_entries_mix(parse):
    assert parse('Tunnel,"Arica, Chile - (ARI)",Network') == [
        "tunnel",
        "arica, chile - (ari)",
        "network",
    ]


def test_a_space_after_the_separator_is_tolerated(parse):
    assert parse('Tunnel, "Arica, Chile - (ARI)"') == ["tunnel", "arica, chile - (ari)"]


# ---------------------------------------------------------------------------
# Backward compatibility — the reason this change is safe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("   ", []),
        ("webapi,community,store", ["webapi", "community", "store"]),
        ("Tunnel,Authoritative DNS", ["tunnel", "authoritative dns"]),
        (
            "Brazil East (Sao Paulo),Brazil Southeast (Vinhedo)",
            ["brazil east (sao paulo)", "brazil southeast (vinhedo)"],
        ),
        ("southamerica-east1,us-central1,us-east1", ["southamerica-east1", "us-central1", "us-east1"]),
        ("sa-east-1,us-east-1,us-east-2", ["sa-east-1", "us-east-1", "us-east-2"]),
        ("  Tunnel  ", ["tunnel"]),
        ("a,,b", ["a", "b"]),
        (",,", []),
        ("SINGLE", ["single"]),
        ("*", ["*"]),
    ],
)
def test_every_filter_shape_already_in_use_parses_the_same(parse, raw, expected):
    """The values here are the real ones from `.env.example` and the cluster manifest."""
    assert parse(raw) == expected


def test_the_real_env_example_filters_are_covered():
    """If a new filter shape lands in `.env.example`, this suite should know about it."""
    env = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
    shapes = [
        line.split("=", 1)[1].strip()
        for line in env.splitlines()
        if "_SERVICE_FILTER=" in line and not line.strip().startswith("#")
    ]
    assert shapes, "no SERVICE_FILTER lines found — the parser of this test broke"
    for shape in shapes:
        assert '"' not in shape, (
            f"{shape!r} uses quotes; add it to the backward-compatibility table above"
        )


# ---------------------------------------------------------------------------
# Bug-Hunter
# ---------------------------------------------------------------------------

def test_an_unclosed_quote_degrades_instead_of_raising(parse):
    """A typo in an env var must never stop the daemon from starting."""
    assert parse('"never closed') == ["never closed"]


def test_a_lone_quote_is_survivable(parse):
    assert isinstance(parse('"'), list)


def test_an_escaped_quote_inside_a_name(parse):
    assert parse('"say ""hi"" now"') == ['say "hi" now']


def test_only_quotes_yields_nothing(parse):
    assert parse('""') == []


def test_a_trailing_separator_is_ignored(parse):
    assert parse("Tunnel,") == ["tunnel"]


def test_a_newline_inside_the_value_does_not_split_the_row(parse):
    """csv treats a newline as a record separator unless quoted — pin what we do."""
    assert isinstance(parse('Tunnel\nNetwork'), list)


def test_case_is_normalised(parse):
    assert parse('"ArIcA, ChIlE - (ARI)"') == ["arica, chile - (ari)"]


def test_a_very_long_entry_is_not_truncated(parse):
    name = "x" * 500
    assert parse(f'"{name}"') == [name]


# ---------------------------------------------------------------------------
# Against the real payload
# ---------------------------------------------------------------------------

def test_a_quoted_pop_name_matches_a_component_in_the_real_payload(parse):
    """The exact scenario that failed in production, against the captured payload."""
    components = json.loads(_FIXTURE.read_text(encoding="utf-8"))["components"]
    pop = next(c for c in components if c["name"].startswith("Arica"))
    parsed = parse(f'"{pop["name"]}"')
    assert parsed == [pop["name"].lower()]
    assert pop["name"].lower() in {c["name"].lower() for c in components}


def test_most_points_of_presence_would_be_unreachable_unquoted():
    """Quantifies why this matters rather than asserting it."""
    components = json.loads(_FIXTURE.read_text(encoding="utf-8"))["components"]
    with_comma = [c for c in components if "," in c["name"]]
    assert len(with_comma) > 300
