"""Operator checks; metadata is not proof of an off-site restore."""
import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from django.conf import settings
from django.utils import timezone


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def backup_status(manifest, *, now=None):
    now = now or timezone.now()
    if not isinstance(manifest, dict):
        return {'ok': False, 'code': 'BACKUP_MANIFEST_INVALID'}
    required = ('source_region', 'destination_region', 'destination_country',
                'retention_days', 'recoverable_through', 'restore_verified_at')
    if any(key not in manifest for key in required):
        return {'ok': False, 'code': 'BACKUP_MANIFEST_INCOMPLETE'}
    if (not all(isinstance(manifest[key], str) and manifest[key].strip()
                for key in ('source_region', 'destination_region')) or
            manifest['source_region'] == manifest['destination_region'] or
            manifest['destination_country'] != 'CN'):
        return {'ok': False, 'code': 'BACKUP_LOCATION_INVALID'}
    if type(manifest['retention_days']) is not int or manifest['retention_days'] < 30:
        return {'ok': False, 'code': 'BACKUP_RETENTION_TOO_SHORT'}
    if manifest['retention_days'] > 30:
        return {'ok': False, 'code': 'BACKUP_RETENTION_TOO_LONG'}
    try:
        recovery = datetime.fromisoformat(manifest['recoverable_through'])
        verified = datetime.fromisoformat(manifest['restore_verified_at'])
        if timezone.is_naive(recovery) or timezone.is_naive(verified):
            raise ValueError
    except (TypeError, ValueError):
        return {'ok': False, 'code': 'BACKUP_TIME_INVALID'}
    if recovery > now or verified > now:
        return {'ok': False, 'code': 'BACKUP_TIME_IN_FUTURE'}
    age = int((now-recovery).total_seconds())
    if now-verified > timedelta(days=30):
        return {'ok': False, 'code': 'BACKUP_RESTORE_CHECK_STALE', 'recovery_age_seconds': age}
    return {'ok': age <= 15*60, 'code': 'BACKUP_FRESH' if age <= 15*60 else 'BACKUP_TOO_OLD',
            'recovery_age_seconds': age, 'retention_days': manifest['retention_days']}


def site_status(origin):
    try:
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError
    except ValueError:
        return {'ok': False, 'code': 'MONITOR_ORIGIN_INVALID'}
    try:
        # urllib verifies TLS normally. Redirects are rejected, not accepted as a
        # healthy login page. No credentials or private response text are logged.
        request = Request(origin.rstrip('/')+'/health/ready', headers={'Accept': 'application/json'})
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            if response.geturl() != request.full_url or response.status != 200:
                return {'ok': False, 'code': 'SITE_NOT_READY'}
            data = response.read(1025)
            if len(data) > 1024 or json.loads(data) != {'status': 'ready'}:
                return {'ok': False, 'code': 'SITE_NOT_READY'}
    except (OSError, ValueError):
        return {'ok': False, 'code': 'SITE_UNAVAILABLE'}
    return {'ok': True, 'code': 'SITE_READY'}


def billing_status():
    from django.db import DatabaseError
    from .commerce.models import BillingIssue
    try:
        count = BillingIssue.objects.exclude(state='resolved').count()
    except DatabaseError:
        return {'ok': False, 'code': 'BILLING_CHECK_UNAVAILABLE'}
    return {'ok': not count, 'code': 'BILLING_ISSUES_OPEN' if count else 'BILLING_CHECK_CLEAR', 'open_issues': count}


def send_fault_alert(code, *, serious):
    """Called only by an explicitly enabled external scheduler; never this chat."""
    if not getattr(settings, 'OPERATIONS_ALERTS_ENABLED', False):
        raise ValueError('OPERATIONS_ALERTS_DISABLED')
    from .email import send_notice as email_notice
    from .sms import send_notice as sms_notice
    sent = {}
    for channel in (('email', 'sms') if serious else ('email',)):
        approved = getattr(settings, 'OPERATIONS_'+channel.upper()+'_TEMPLATE_APPROVED', False)
        template = getattr(settings, 'OPERATIONS_'+channel.upper()+'_TEMPLATE_ID', '')
        recipient = getattr(settings, 'OPERATIONS_'+channel.upper()+'_RECIPIENT', '')
        if not approved or not template or not recipient:
            sent[channel] = 'unavailable'
            continue
        try:
            reference = email_notice(recipient, template, {'code': code}) if channel == 'email' else sms_notice(recipient, template, [code])
            sent[channel] = 'sent' if reference else 'unknown'
        except OSError:
            sent[channel] = 'unknown'
    return sent
