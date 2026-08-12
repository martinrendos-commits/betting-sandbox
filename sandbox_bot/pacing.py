"""Polite pacing between requests.

This is rate limiting, not evasion: the jitter exists so repeated polling does
not hammer the target in a tight loop, and so several bot instances do not
synchronise on the same instant. It is deliberately *not* an attempt to look
human to an anti-bot system.
"""

from __future__ import annotations

import random
import time


def sleep_between_polls(interval_s: float, jitter: float = 0.2) -> None:
    """Sleep ``interval_s`` +/- ``jitter`` fraction, never less than 0.5 s."""
    factor = 1.0 + random.uniform(-jitter, jitter)
    time.sleep(max(0.5, interval_s * factor))
