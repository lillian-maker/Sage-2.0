"""Shared runtime clock for the workbench (dev/test only override).

Runtime code (services, routes) must take the current instant from here, not from
django.utils.timezone directly, so that SAGE_SEED_NOW pins both the seeded data and
any "past 7 days" windows / relative times computed at render time. The override is
honoured only when settings.ENVIRONMENT is development or test; production always
uses the real clock.
"""
import os
from datetime import datetime, time

from django.conf import settings
from django.utils import timezone as _django_timezone


def now():
    raw = os.environ.get('SAGE_SEED_NOW', '').strip()
    if not raw or settings.ENVIRONMENT not in ('development', 'test'):
        return _django_timezone.now()
    if len(raw) <= 5:
        hour, minute = (int(part) for part in raw.split(':'))
        return _django_timezone.make_aware(datetime.combine(_django_timezone.localdate(), time(hour, minute)))
    value = datetime.fromisoformat(raw)
    return value if _django_timezone.is_aware(value) else _django_timezone.make_aware(value)


def localdate():
    return _django_timezone.localtime(now()).date()
