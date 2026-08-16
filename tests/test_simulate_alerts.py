"""Tests for the verdict logic of the alert-firing simulation.

The script drives nine real modules against real payloads and cannot run offline. What
can — and must — run offline is the judgement it passes: deciding whether a provider
alerted, whether it recovered, and *why* it did not.

That last part is the reason this suite exists rather than a single "did it pass" check.
The run that produced this script found two providers alerting and never recovering, and
"never recovered" as a verdict is nearly useless: it reads like the module is broken when
the module is fine and the state machine is the one at fault. The distinction is encoded
in `route_asymmetry`, so it gets tested like any other decision.

The degradation functions are deliberately not tested here. They are assertions about
the shape of somebody else's payload, and a fixture-based test of them would only prove
they are self-consistent — the real check is the script running against the live feed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from simulate_alerts import (  # noqa: E402
    PER_MODULE,
    PER_SERVICE,
    PROVIDERS,
    Outcome,
    route_for,
    verdicts,
)


def _ok(slug="steam", **overrides):
    """An outcome for a provider that alerted and recovered cleanly."""
    fields = dict(
        slug=slug,
        note="fixture; component -> down",
        healthy_status="OK",
        alert_status="ALERT",
        alert_channels=4,
        alert_reason="Comp: major_outage",
        recovery_channels=4,
        alert_route=PER_SERVICE,
        recovery_route=PER_SERVICE,
    )
    fields.update(overrides)
    return Outcome(**fields)


# ---------------------------------------------------------------------------
# route_for — the branch the state machine will take for a given payload
# ---------------------------------------------------------------------------

def test_a_non_empty_list_of_dicts_routes_per_service():
    assert route_for([{"id": "a", "name": "A"}]) == PER_SERVICE


def test_an_empty_list_routes_per_module():
    """`all([])` is True, so `_extract_service_items` returns a falsy `[]` here."""
    assert route_for([]) == PER_MODULE


def test_a_dict_routes_per_module():
    assert route_for({"hero": "degraded"}) == PER_MODULE


def test_none_routes_per_module():
    assert route_for(None) == PER_MODULE


def test_a_list_of_non_dicts_routes_per_module():
    assert route_for(["a", "b"]) == PER_MODULE


def test_a_list_mixing_dicts_and_non_dicts_routes_per_module():
    assert route_for([{"id": "a"}, "b"]) == PER_MODULE


def test_route_for_agrees_with_the_state_machine_itself():
    """Pin the helper to the function it mirrors, so they cannot drift apart."""
    from app.core.notifications import _extract_service_items

    for payload in ([{"id": "a"}], [], {}, None, ["x"], [{"id": "a"}, "b"]):
        expected = PER_SERVICE if _extract_service_items(payload) else PER_MODULE
        assert route_for(payload) == expected, payload


# ---------------------------------------------------------------------------
# verdicts — the happy path
# ---------------------------------------------------------------------------

def test_no_outcomes_is_no_failures():
    assert verdicts([]) == []


def test_a_provider_that_alerted_and_recovered_is_not_a_failure():
    assert verdicts([_ok()]) == []


def test_every_provider_healthy_is_no_failures():
    assert verdicts([_ok(f"p{i}") for i in range(9)]) == []


def test_a_per_module_provider_that_recovers_is_not_a_failure():
    """rockstar's payload is a dict in both phases — symmetric, and fine."""
    outcome = _ok("rockstar", alert_route=PER_MODULE, recovery_route=PER_MODULE)
    assert verdicts([outcome]) == []


# ---------------------------------------------------------------------------
# verdicts — failing to alert
# ---------------------------------------------------------------------------

def test_a_provider_that_stayed_ok_under_degradation_fails():
    failures = verdicts([_ok("aws", alert_status="OK", alert_channels=0)])
    assert len(failures) == 1
    assert failures[0].startswith("aws:")
    assert "OK" in failures[0]


def test_alert_status_without_delivery_still_fails():
    """ALERT that reached zero channels is a delivery bug, not a detection one."""
    failures = verdicts([_ok(alert_channels=0)])
    assert len(failures) == 1
    assert "0 channel(s)" in failures[0]


def test_a_provider_with_no_alert_status_at_all_says_nothing():
    failures = verdicts([_ok(alert_status=None, alert_channels=0)])
    assert "nothing" in failures[0]


def test_not_alerting_takes_precedence_over_not_recovering():
    """A provider that never alerted cannot meaningfully fail to recover."""
    failures = verdicts([_ok(alert_status="OK", alert_channels=0, recovery_channels=0)])
    assert len(failures) == 1
    assert "recover" not in failures[0]


# ---------------------------------------------------------------------------
# verdicts — failing to recover, and telling the two causes apart
# ---------------------------------------------------------------------------

def test_route_asymmetry_is_reported_with_its_cause():
    """This is the aws/gcp defect: per-service alert, per-module recovery lookup."""
    failures = verdicts([_ok("gcp", recovery_channels=0, recovery_route=PER_MODULE)])
    assert len(failures) == 1
    message = failures[0]
    assert "gcp:" in message
    assert PER_SERVICE in message and PER_MODULE in message
    assert "empty list" in message


def test_a_symmetric_recovery_failure_is_reported_without_blaming_the_route():
    """Both phases on the same branch: the cause is elsewhere, do not misattribute it."""
    failures = verdicts([_ok(recovery_channels=0)])
    assert len(failures) == 1
    assert "empty list" not in failures[0]
    assert PER_SERVICE in failures[0]


def test_the_reverse_asymmetry_is_not_treated_as_the_known_defect():
    """per-module alert -> per-service recovery is a different bug, worded differently."""
    outcome = _ok(recovery_channels=0, alert_route=PER_MODULE, recovery_route=PER_SERVICE)
    failures = verdicts([outcome])
    assert "empty list" not in failures[0]


def test_a_recovery_failure_reports_how_many_channels_did_get_the_alert():
    failures = verdicts([_ok("aws", alert_channels=12, recovery_channels=0,
                             recovery_route=PER_MODULE)])
    assert "12 channel(s)" in failures[0]


def test_route_asymmetry_property_is_false_when_recovery_worked():
    assert _ok().route_asymmetry is False


def test_route_asymmetry_property_is_true_for_the_known_shape():
    assert _ok(recovery_route=PER_MODULE).route_asymmetry is True


# ---------------------------------------------------------------------------
# verdicts — errors short-circuit everything else
# ---------------------------------------------------------------------------

def test_an_errored_provider_reports_the_error_and_nothing_else():
    failures = verdicts([Outcome("oci", "", error="could not degrade: KeyError('items')")])
    assert len(failures) == 1
    assert "could not degrade" in failures[0]


def test_an_error_is_not_also_reported_as_a_missing_alert():
    failures = verdicts([Outcome("oci", "", error="boom")])
    assert "degradation produced" not in failures[0]


def test_failures_are_reported_one_per_provider():
    outcomes = [
        _ok("steam"),
        _ok("aws", alert_channels=12, recovery_channels=0, recovery_route=PER_MODULE),
        _ok("gcp", recovery_channels=0, recovery_route=PER_MODULE),
        Outcome("oci", "", error="boom"),
    ]
    failures = verdicts(outcomes)
    assert len(failures) == 3
    assert [f.split(":")[0] for f in failures] == ["aws", "gcp", "oci"]


# ---------------------------------------------------------------------------
# Outcome — the two predicates the report and the exit code both read
# ---------------------------------------------------------------------------

def test_alerted_requires_both_the_status_and_a_delivery():
    assert _ok().alerted is True
    assert _ok(alert_channels=0).alerted is False
    assert _ok(alert_status="OK").alerted is False


def test_an_error_status_does_not_count_as_an_alert():
    assert _ok(alert_status="ERROR").alerted is False


def test_recovered_is_purely_about_delivery():
    assert _ok().recovered is True
    assert _ok(recovery_channels=0).recovered is False


# ---------------------------------------------------------------------------
# The provider table itself
# ---------------------------------------------------------------------------

def test_every_configured_module_has_a_provider_entry():
    """A module added without a degradation would silently never be simulated."""
    modules = {p.name for p in (Path(__file__).resolve().parent.parent / "app" / "modules").iterdir()
               if p.is_dir() and not p.name.startswith("_")}
    assert {p.slug for p in PROVIDERS} == modules


def test_provider_slugs_are_unique():
    slugs = [p.slug for p in PROVIDERS]
    assert len(slugs) == len(set(slugs))


def test_every_declared_fixture_exists():
    fixtures = Path(__file__).resolve().parent / "fixtures"
    for provider in PROVIDERS:
        if provider.fixture:
            assert (fixtures / provider.fixture).exists(), provider.slug


def test_every_provider_declares_a_degradation():
    assert all(callable(p.degrade) for p in PROVIDERS)
