"""One verified user's trial. No task execution, URL fetching or public completion.

The future trusted executor must verify a complete report before delivering an
event here. A hash binds stored content; it does not prove report quality.
"""
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.db import transaction

from .commerce.billing import _get, _identifier
from .account import trial_identity_blocked
from .models import TrialAttempt, TrialEvent


def validate_trial_input(scenario, value):
    if scenario not in ('voc', 'shopify_diagnosis'):
        raise ValueError('INVALID_TRIAL_SCENARIO')
    if (not isinstance(value, str) or not value.strip() or len(value) > 2048 or
        any(unicodedata.category(char).startswith('C') for char in value)):
        raise ValueError('INVALID_TRIAL_INPUT')
    normalized = unicodedata.normalize('NFC', value.strip())
    if scenario == 'voc':
        if len(normalized) > 200 or '://' in normalized or normalized.lower().startswith('www.'):
            raise ValueError('INVALID_TRIAL_INPUT')
    else:
        try:
            parsed = urlsplit(normalized)
            host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        except (ValueError, UnicodeError) as exc:
            raise ValueError('INVALID_TRIAL_INPUT') from exc
        # Syntax restriction only. The eventual fetcher must resolve and validate
        # every connection and redirect; no DNS verification is claimed here.
        if (parsed.scheme != 'https' or parsed.netloc.lower() != host or len(host) > 253 or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or
            '\\' in normalized or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host) or
            host.rsplit('.', 1)[-1] in ('local', 'internal', 'localhost', 'invalid', 'test', 'example', 'onion')):
            raise ValueError('INVALID_TRIAL_INPUT')
        normalized = 'https://' + host + '/'
    key = hashlib.sha256((scenario + '\0' + normalized).encode()).hexdigest()
    return normalized, key


def _lock_user(user):
    if not user or not user.is_authenticated:
        raise ValueError('FORBIDDEN')
    result = get_user_model().objects.select_for_update().filter(pk=user.pk, is_active=True).first()
    if not result:
        raise ValueError('FORBIDDEN')
    return result


def _current(user):
    return TrialAttempt.objects.filter(user=user).exclude(task_state='irrecoverable_failure').first()


def _result(user, attempt=None):
    active = _current(user)
    eligibility = 'consumed' if active and active.task_state == 'completed' else 'locked' if active else 'available'
    if not active and trial_identity_blocked(user):
        eligibility = 'consumed'
    attempt = attempt or active
    return {'eligibility': eligibility, 'attempt': None if not attempt else {
        'id': str(attempt.pk), 'scenario': attempt.scenario, 'input_value': attempt.input_value,
        'task_state': attempt.task_state, 'report': attempt.report}}


@transaction.atomic
def read_trial(user, attempt_id=None):
    user = _lock_user(user)
    attempt = _get(TrialAttempt.objects.filter(user=user), pk=attempt_id) if attempt_id is not None else None
    return _result(user, attempt)


@transaction.atomic
def start_trial(user, scenario, value, request_key):
    """Internal executor call only; creation is not proof a task ran."""
    user = _lock_user(user)
    value, key = validate_trial_input(scenario, value)
    _identifier(request_key)
    previous = TrialAttempt.objects.filter(user=user, request_key=request_key).first()
    if previous:
        if previous.scenario != scenario or previous.input_key != key:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _result(user, previous)
    active = _current(user)
    if active:
        if active.task_state == 'completed':
            raise ValueError('TRIAL_ALREADY_USED')
        if active.scenario != scenario or active.input_key != key:
            raise ValueError('TRIAL_ORIGINAL_TASK_REQUIRED')
        return _result(user, active)
    if trial_identity_blocked(user):
        raise ValueError('TRIAL_ALREADY_USED')
    attempt = TrialAttempt.objects.create(user=user, scenario=scenario, input_value=value,
        input_key=key, request_key=request_key)
    return _result(user, attempt)


def _event(user, attempt_id, event_key, kind, report, evidence_hash):
    _identifier(event_key)
    attempt = _get(TrialAttempt.objects.select_for_update(), user=user, pk=attempt_id)
    try:
        body = json.dumps({'kind': kind, 'report': report, 'evidence': evidence_hash}, sort_keys=True,
                          separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('TRIAL_RESULT_NOT_READY') from exc
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    previous = TrialEvent.objects.filter(attempt=attempt, event_key=event_key).first()
    if previous:
        if previous.request_hash != fingerprint:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _result(user, attempt)
    if attempt.task_state == 'irrecoverable_failure':
        raise ValueError('STALE_TRIAL_ATTEMPT')
    if attempt.task_state == 'completed':
        raise ValueError('INVALID_TRIAL_TRANSITION')
    states = {'cancel': 'cancelled', 'retry': 'running', 'recoverable_failure': 'recoverable_failure',
              'irrecoverable_failure': 'irrecoverable_failure', 'complete': 'completed'}
    if kind not in states:
        raise ValueError('INVALID_TRIAL_ACTION')
    if kind in ('complete', 'recoverable_failure', 'irrecoverable_failure'):
        if kind == 'complete':
            if not isinstance(report, dict) or not report or len(body.encode()) > 1048576:
                raise ValueError('TRIAL_RESULT_NOT_READY')
            encoded = json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
            if hashlib.sha256(encoded.encode()).hexdigest() != evidence_hash:
                raise ValueError('TRIAL_EVIDENCE_MISMATCH')
            attempt.report = report
        elif not isinstance(evidence_hash, str) or not re.fullmatch('[a-f0-9]{64}', evidence_hash):
            raise ValueError('TRIAL_EVIDENCE_REQUIRED')
    attempt.task_state = states[kind]
    attempt.save(update_fields=['task_state', 'report', 'updated_at'])
    TrialEvent.objects.create(attempt=attempt, event_key=event_key, kind=kind,
                              request_hash=fingerprint, evidence_hash=evidence_hash)
    return _result(user, attempt)


@transaction.atomic
def update_trial(user, attempt_id, action, event_key):
    user = _lock_user(user)
    if action not in ('cancel', 'retry'):
        raise ValueError('INVALID_TRIAL_ACTION')
    return _event(user, attempt_id, event_key, action, None, '')


@transaction.atomic
def record_trial_event(user, attempt_id, event_key, kind, *, report=None, evidence_hash=''):
    user = _lock_user(user)
    if kind not in ('complete', 'recoverable_failure', 'irrecoverable_failure'):
        raise ValueError('INVALID_TRIAL_ACTION')
    return _event(user, attempt_id, event_key, kind, report, evidence_hash)
