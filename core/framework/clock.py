from datetime import datetime, timezone


class Clock:
    def now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FixedClock(Clock):
    def __init__(self, stamp: str):
        self.stamp = stamp

    def now(self) -> str:
        return self.stamp
