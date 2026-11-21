"""Delegate to the shared workbench clock so seeds and runtime share one entry."""
from backend.workbench.clock import localdate, now
from django.utils import timezone as _django_timezone


class _SeedTimezone:
    """Drop-in for django.utils.timezone inside seed modules: now()/localdate() follow the seed clock."""
    now = staticmethod(now)
    localdate = staticmethod(localdate)

    def __getattr__(self, name):
        return getattr(_django_timezone, name)


timezone = _SeedTimezone()
