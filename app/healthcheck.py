"""Probe command: `python -m app.healthcheck [max_age_seconds]` (#134).

Exits 0 when the liveness file was touched less than `max_age_seconds` ago (default
120), 1 otherwise, with the reason on one line. Missing file, a bad argument and
liveness turned off all fail: a probe that cannot see the signal must not pass.
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Optional, Sequence

from .core.config import LIVENESS_PATH_ENV, liveness_path_from_env

DEFAULT_MAX_AGE_SECONDS = 120.0


def check(path: Optional[str], max_age_seconds: float, now: float) -> tuple:
    """(exit code, reason)."""
    if not path:
        return 1, f"liveness is off: {LIVENESS_PATH_ENV} is empty"
    try:
        age = now - os.path.getmtime(path)
    except OSError as exc:
        return 1, f"liveness file unreadable: {type(exc).__name__}: {exc}"
    if age > max_age_seconds:
        return 1, f"liveness file is {age:.0f}s old (max {max_age_seconds:.0f}s)"
    if age < -max_age_seconds:
        # Touched "in the future": the clock stepped back, and the age no longer says
        # anything. Failing restarts the pod and the file starts over on the new clock.
        return 1, f"liveness file is {-age:.0f}s in the future"
    return 0, f"ok: liveness file is {age:.0f}s old"


def main(argv: Sequence[str]) -> int:
    max_age = DEFAULT_MAX_AGE_SECONDS
    if argv:
        try:
            max_age = float(argv[0])
        except ValueError:
            max_age = -1.0
        if not (math.isfinite(max_age) and max_age > 0):
            print(f"invalid max_age_seconds: {argv[0]!r}")
            return 1
    code, reason = check(liveness_path_from_env(), max_age, time.time())
    print(reason)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
