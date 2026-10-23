from __future__ import annotations

import random


def backoff_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Exponential backoff with 25% jitter; attempt is zero-based."""

    delay = min(max_delay, base_delay * (2**attempt))
    return delay * (0.75 + random.random() * 0.5)
