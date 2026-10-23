import asyncio
import re
import time
from typing import Any


class RateLimiter:
    """Async sliding-window rate limiter with request spacing and dynamic backoff."""

    def __init__(self, rpm: int = 5):
        self.rpm = max(1, rpm)
        self.min_interval = 60.0 / self.rpm
        self._lock = asyncio.Lock()
        self._timestamps: list[float] = []
        self._last_call: float = 0.0
        self._pause_until: float = 0.0

    async def acquire(self) -> float:
        """Wait until a slot is available under the RPM limit and spacing rule.
        Returns the seconds waited (if any)."""
        t_start = time.time()
        while True:
            async with self._lock:
                now = time.time()

                # If under a penalty hold (e.g. from Google 429 retryDelay), wait it out
                if now < self._pause_until:
                    wait_time = self._pause_until - now
                else:
                    # Prune timestamps older than 60s
                    cutoff = now - 60.0
                    self._timestamps = [t for t in self._timestamps if t > cutoff]

                    # Check window capacity
                    if len(self._timestamps) >= self.rpm:
                        wait_time = max(0.1, (self._timestamps[0] + 60.0) - now + 0.1)
                    else:
                        # Check request spacing
                        elapsed = now - self._last_call
                        if elapsed < self.min_interval:
                            wait_time = self.min_interval - elapsed
                        else:
                            # Slot acquired!
                            self._timestamps.append(now)
                            self._last_call = now
                            return round(time.time() - t_start, 2)

            # Sleep outside the lock so other tasks aren't blocked from checking
            await asyncio.sleep(min(wait_time, 35.0))

    def penalize(self, seconds: float) -> None:
        """Pause the limiter for a specified penalty duration (e.g. on 429)."""
        now = time.time()
        self._pause_until = max(self._pause_until, now + seconds)

    @classmethod
    def extract_retry_delay(cls, e: Exception) -> float:
        """Parse retryDelay from Google API errors or return a sensible default."""
        try:
            details = getattr(e, "details", [])
            for d in details:
                if isinstance(d, dict) and "retryDelay" in d:
                    val = str(d["retryDelay"]).rstrip("s")
                    return min(float(val) + 1.0, 35.0)
        except Exception:
            pass
        s = str(e)
        m = re.search(r"retryDelay':\s*'([0-9\.]+)s?'", s) or re.search(r"Please retry in ([0-9\.]+)s", s)
        if m:
            try:
                return min(float(m.group(1)) + 1.0, 35.0)
            except Exception:
                pass
        return 15.0
