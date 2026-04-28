"""Dispatch time window controls for Phase B."""

from __future__ import annotations

import time
from datetime import datetime, time as dt_time, timedelta


class TimeWindowController:
    """Allow dispatch only inside configured daily time windows."""

    def __init__(
        self,
        *,
        start_hour: int = 0,
        end_hour: int = 24,
        weekdays: set[int] | None = None,
    ) -> None:
        if not 0 <= start_hour <= 23:
            raise ValueError("start_hour must be between 0 and 23")
        if not 1 <= end_hour <= 24:
            raise ValueError("end_hour must be between 1 and 24")
        if start_hour >= end_hour:
            raise ValueError("start_hour must be smaller than end_hour")

        self.start_hour = start_hour
        self.end_hour = end_hour
        self.weekdays = weekdays or {0, 1, 2, 3, 4, 5, 6}

    def is_open(self, current_time: datetime | None = None) -> bool:
        """Return whether dispatch is allowed at the given time."""
        current = current_time or datetime.now()
        if current.weekday() not in self.weekdays:
            return False
        return self.start_hour <= current.hour < self.end_hour

    def seconds_until_open(self, current_time: datetime | None = None) -> int:
        """Return the number of seconds until the next open window starts."""
        current = current_time or datetime.now()
        if self.is_open(current):
            return 0

        next_start = self.next_open_time(current)
        return max(0, int((next_start - current).total_seconds()))

    def next_open_time(self, current_time: datetime | None = None) -> datetime:
        """Return the next datetime when dispatch becomes allowed."""
        current = current_time or datetime.now()
        candidate = current.replace(
            hour=self.start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

        if current.weekday() in self.weekdays and current < candidate:
            return candidate

        for day_offset in range(0, 8):
            probe = current + timedelta(days=day_offset)
            if probe.weekday() not in self.weekdays:
                continue

            probe_start = probe.replace(
                hour=self.start_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if probe_start > current:
                return probe_start

        raise RuntimeError("unable to determine next open time")

    def wait_until_open(self, poll_interval: int = 30) -> None:
        """Sleep until the current time is inside the configured time window."""
        while not self.is_open():
            time.sleep(min(poll_interval, max(1, self.seconds_until_open())))

    def current_window(self) -> tuple[dt_time, dt_time]:
        """Expose the configured daily window bounds."""
        end_hour = 23 if self.end_hour == 24 else self.end_hour
        end_minute = 59 if self.end_hour == 24 else 0
        return dt_time(self.start_hour, 0), dt_time(end_hour, end_minute)
