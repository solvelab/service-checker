"""Tests for the retry/classification logic of the live endpoint simulation.

The script itself queries nine real providers and cannot be tested offline. What can —
and must — be tested is the decision it makes: telling a provider hiccup apart from a
module that is genuinely broken. That distinction is the whole point of the tool, and
getting it wrong in either direction is costly. Too eager to forgive and a real
blindness is reported as noise; too strict and the team learns to ignore a red run.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from simulate_endpoints import resolve_with_retry  # noqa: E402


def _runner(outcomes):
    """Build a runner whose answer per slug comes from a queue of booleans."""
    calls: list[str] = []

    async def runner(slug):
        calls.append(slug)
        queue = outcomes[slug]
        ok = queue.pop(0) if queue else True
        return ok, "" if ok else "still failing"

    return runner, calls


async def _noop_sleep(_seconds):
    return None


def _resolve(failures, outcomes, attempts=2):
    runner, calls = _runner(outcomes)
    transient, persistent = asyncio.run(
        resolve_with_retry(failures, runner, attempts, 0.0, sleep=_noop_sleep)
    )
    return transient, persistent, calls


# ---------------------------------------------------------------------------
# The core distinction
# ---------------------------------------------------------------------------

def test_a_failure_that_does_not_repeat_is_transient():
    transient, persistent, _ = _resolve({"aws": "timeout"}, {"aws": [True]})
    assert transient == {"aws": "timeout"}
    assert persistent == {}


def test_a_failure_that_repeats_is_persistent():
    transient, persistent, _ = _resolve({"aws": "blind"}, {"aws": [False]})
    assert transient == {}
    assert persistent == {"aws": "blind"}


def test_the_original_detail_is_kept_not_the_retry_detail():
    """The first failure is the informative one; the retry only answers yes or no."""
    _, persistent, _ = _resolve({"aws": "fields absent upstream: arn"}, {"aws": [False]})
    assert persistent["aws"] == "fields absent upstream: arn"


def test_transient_and_persistent_are_separated_in_one_run():
    transient, persistent, _ = _resolve(
        {"aws": "hiccup", "gcp": "blind"}, {"aws": [True], "gcp": [False]}
    )
    assert transient == {"aws": "hiccup"}
    assert persistent == {"gcp": "blind"}


# ---------------------------------------------------------------------------
# Only failures are retried
# ---------------------------------------------------------------------------

def test_only_the_failing_modules_are_retried():
    _, _, calls = _resolve({"aws": "hiccup"}, {"aws": [True]})
    assert calls == ["aws"]


def test_a_module_that_recovers_is_not_queried_again():
    """Three attempts, recovered on the first retry — it must not be asked twice."""
    _, _, calls = _resolve({"aws": "hiccup"}, {"aws": [True, False]}, attempts=3)
    assert calls == ["aws"]


def test_nothing_to_retry_calls_the_runner_at_all():
    transient, persistent, calls = _resolve({}, {})
    assert (transient, persistent, calls) == ({}, {}, [])


# ---------------------------------------------------------------------------
# Attempt budget
# ---------------------------------------------------------------------------

def test_one_attempt_disables_the_retry_entirely():
    """SIMULATE_ATTEMPTS=1 must restore the previous, unforgiving behaviour."""
    transient, persistent, calls = _resolve(
        {"aws": "blind"}, {"aws": [True]}, attempts=1
    )
    assert persistent == {"aws": "blind"}
    assert transient == {}
    assert calls == []


@pytest.mark.parametrize("attempts", [0, -3])
def test_a_nonsensical_attempt_count_behaves_as_one(attempts):
    _, persistent, calls = _resolve({"aws": "blind"}, {"aws": [True]}, attempts=attempts)
    assert persistent == {"aws": "blind"}
    assert calls == []


def test_three_attempts_give_two_retries():
    _, persistent, calls = _resolve(
        {"aws": "blind"}, {"aws": [False, False]}, attempts=3
    )
    assert persistent == {"aws": "blind"}
    assert calls == ["aws", "aws"]


def test_recovering_on_the_last_attempt_still_counts_as_transient():
    transient, persistent, _ = _resolve(
        {"aws": "hiccup"}, {"aws": [False, True]}, attempts=3
    )
    assert transient == {"aws": "hiccup"}
    assert persistent == {}


# ---------------------------------------------------------------------------
# Adversarial
# ---------------------------------------------------------------------------

def test_a_runner_that_raises_is_not_swallowed():
    """A bug in the retry path must surface, not be mistaken for a healthy module."""

    async def exploding(_slug):
        raise RuntimeError("runner is broken")

    with pytest.raises(RuntimeError, match="runner is broken"):
        asyncio.run(
            resolve_with_retry({"aws": "x"}, exploding, 2, 0.0, sleep=_noop_sleep)
        )


def test_every_failure_lands_in_exactly_one_bucket():
    failures = {f"m{i}": f"reason {i}" for i in range(10)}
    outcomes = {f"m{i}": [i % 2 == 0] for i in range(10)}
    transient, persistent, _ = _resolve(failures, outcomes)
    assert set(transient) | set(persistent) == set(failures)
    assert not (set(transient) & set(persistent))


def test_the_sleeper_is_awaited_between_attempts_not_before_the_first():
    slept: list[float] = []

    async def spy(seconds):
        slept.append(seconds)

    runner, _ = _runner({"aws": [False, True]})
    asyncio.run(resolve_with_retry({"aws": "x"}, runner, 3, 1.5, sleep=spy))
    assert slept == [1.5, 1.5]


def test_no_sleep_happens_when_there_is_nothing_to_retry():
    slept: list[float] = []

    async def spy(seconds):
        slept.append(seconds)

    runner, _ = _runner({})
    asyncio.run(resolve_with_retry({}, runner, 3, 1.5, sleep=spy))
    assert slept == []


# ---------------------------------------------------------------------------
# One fetch per module (#41)
#
# The contract check used to fetch the upstream a second time. That doubled the load
# on nine third-party services and, worse, read a different snapshot: when an incident
# opens or closes between the two reads, `raw` and `parsed` describe different moments,
# and comparing those two columns is the one thing an operator reads them for.
# ---------------------------------------------------------------------------

from simulate_endpoints import (  # noqa: E402
    RecordingClient,
    check_contract,
    extract_records,
    inspect_upstream,
)


class _Response:
    def __init__(self, payload=None, text="", raises=False):
        self._payload = payload
        self.text = text
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, response=None, raises=None):
        self.gets: list[str] = []
        self._response = response
        self._raises = raises

    async def get(self, url, **_kwargs):
        self.gets.append(str(url))
        if self._raises:
            raise self._raises
        return self._response


def _inspect(slug, url, client):
    return asyncio.run(inspect_upstream(slug, url, client))


# --- the recording wrapper -------------------------------------------------

def test_the_wrapper_remembers_the_response_per_url():
    response = _Response(payload=[{"arn": "a"}])
    client = RecordingClient(_Client(response))
    asyncio.run(client.get("https://example.com/a"))
    assert client.responses["https://example.com/a"] is response


def test_the_wrapper_passes_the_call_through():
    inner = _Client(_Response(payload=[]))
    asyncio.run(RecordingClient(inner).get("https://example.com/a"))
    assert inner.gets == ["https://example.com/a"]


def test_the_wrapper_exposes_attributes_of_the_wrapped_client():
    inner = _Client(_Response())
    inner.timeout = 42
    assert RecordingClient(inner).timeout == 42


# --- the point of the change ----------------------------------------------

def test_a_recorded_response_is_reused_instead_of_refetched():
    url = "https://health.aws.amazon.com/public/currentevents"
    inner = _Client(_Response(payload=[{"arn": "a", "service": "s", "status": "1", "summary": "x"}]))
    client = RecordingClient(inner)
    asyncio.run(client.get(url))
    inner.gets.clear()

    records, missing, note = _inspect("aws", url, client)

    assert inner.gets == []          # no second request
    assert (records, missing, note) == (1, [], "")


def test_a_module_that_never_reached_the_url_falls_back_to_fetching():
    """It failed before the request, so there is nothing recorded to reuse."""
    url = "https://health.aws.amazon.com/public/currentevents"
    inner = _Client(_Response(payload=[{"arn": "a", "service": "s", "status": "1", "summary": "x"}]))
    client = RecordingClient(inner)

    records, _missing, _note = _inspect("aws", url, client)

    assert inner.gets == [url]
    assert records == 1


def test_a_plain_client_without_recording_still_works():
    """The retry path passes a fresh client; it must not depend on the wrapper."""
    url = "https://health.aws.amazon.com/public/currentevents"
    inner = _Client(_Response(payload=[{"arn": "a", "service": "s", "status": "1", "summary": "x"}]))
    assert _inspect("aws", url, inner)[0] == 1


def test_a_module_without_a_contract_never_fetches():
    inner = _Client(_Response())
    records, _missing, note = _inspect("steam", "https://steamstat.us/", RecordingClient(inner))
    assert inner.gets == []
    assert records == -1
    assert "no contract" in note


def test_a_fetch_failure_on_the_fallback_is_reported_not_raised():
    inner = _Client(raises=OSError("down"))
    records, _missing, note = _inspect("aws", "https://x", RecordingClient(inner))
    assert records == -1
    assert "fetch failed" in note


# --- extraction, pure ------------------------------------------------------

def test_extract_reads_a_json_list():
    records, note = extract_records("aws", _Response(payload=[{"arn": "a"}, {"arn": "b"}]))
    assert len(records) == 2 and note is None


def test_extract_reads_a_json_collection():
    response = _Response(payload={"components": [{"id": "a"}, {"id": "b"}]})
    assert len(extract_records("github", response)[0]) == 2


def test_extract_reads_rss_items():
    response = _Response(text="<rss><item><title>a</title></item><item><title>b</title></item></rss>")
    assert len(extract_records("oci", response)[0]) == 2


def test_extract_reports_a_non_json_body():
    assert extract_records("aws", _Response(raises=True))[1] == "body is not JSON"


def test_extract_of_an_unexpected_shape_yields_no_records():
    assert extract_records("aws", _Response(payload={"nope": 1}))[0] == []


def test_extract_skips_non_dict_entries():
    assert len(extract_records("aws", _Response(payload=["x", 1, {"arn": "a"}]))[0]) == 1


# --- contract checking, pure ----------------------------------------------

def test_check_contract_flags_a_field_absent_from_every_record():
    count, missing, _note = check_contract("aws", [{"service": "s"}, {"service": "t"}])
    assert count == 2
    assert "arn" in missing


def test_check_contract_accepts_a_field_present_in_any_record():
    _count, missing, _note = check_contract(
        "aws", [{"arn": "a", "service": "s", "status": "1", "summary": "x"}, {"service": "t"}]
    )
    assert missing == []


def test_check_contract_on_no_records_is_not_a_failure():
    count, missing, note = check_contract("aws", [])
    assert (count, missing) == (0, [])
    assert "no records" in note
