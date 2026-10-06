"""Public intake and restricted sales mutations; never send messages or log PII."""
import json
import logging
import re
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from .http import client_ip, error
from .models import Consultation, ConsultationAudit
from .views import digest, payload, throttle


@sensitive_variables()
def normalized_fields(data):
    limits = {'company': 160, 'phone': 32, 'email': 254, 'question': 2000}
    cleaned = {}
    for field, limit in limits.items():
        value = data.get(field)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('INVALID_REQUEST')
        value = value.strip()
        if field != 'email' and not value:
            raise ValueError('INVALID_REQUEST')
        if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('INVALID_REQUEST')
        cleaned[field] = value
    # International E.164, with visual separators accepted; no country guessed.
    phone = re.sub(r'[ ()-]', '', cleaned['phone'])
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', phone):
        raise ValueError('INVALID_PHONE')
    cleaned['phone'] = phone
    if cleaned['email']:
        try:
            validate_email(cleaned['email'])
        except ValidationError as exc:
            raise ValueError('INVALID_EMAIL') from exc
    return cleaned


@sensitive_post_parameters()
@sensitive_variables()
def submit(request):
    if request.method != 'POST':
        response = error(request, 'METHOD_NOT_ALLOWED', 405)
        response['Allow'] = 'POST'
        return response
    try:
        fields = normalized_fields(payload(request, ('company', 'phone', 'email', 'question')))
        request_key = request.headers.get('Idempotency-Key', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', request_key):
            raise ValueError('INVALID_IDEMPOTENCY_KEY')
    except ValueError as exc:
        return error(request, str(exc), 400, '请检查公司、国际格式手机号、邮箱及问题描述。')
    # Bind retry keys to the anonymous CSRF/session context, not globally guessable keys.
    binding = request.session.session_key
    if not binding:
        return error(request, 'SESSION_REQUIRED', 403, '请刷新页面后重试。')
    key = digest('consultation:' + binding + ':' + request_key)
    fingerprint = digest(json.dumps(fields, sort_keys=True, ensure_ascii=False))
    try:
        with transaction.atomic():
            if not throttle('consultation-ip:' + client_ip(request), 10):
                return error(request, 'RATE_LIMITED', 429, '提交较频繁，请稍后重试。')
            if not throttle('consultation-session:' + binding, 20):
                return error(request, 'RATE_LIMITED', 429, '提交较频繁，请稍后重试。')
            # The session throttle row serializes retries even if the IP changes.
            record = Consultation.objects.filter(request_key=key).first()
            if record:
                if record.request_hash != fingerprint:
                    return error(request, 'IDEMPOTENCY_CONFLICT', 409, '请为新的申请重新提交。')
            else:
                record = Consultation.objects.create(**fields, request_key=key, request_hash=fingerprint)
                ConsultationAudit.objects.create(consultation=record, action='received',
                                                 request_id=request.request_id)
    except DatabaseError:
        logging.getLogger('sage').error('consultation_database_unavailable request_id=%s', request.request_id)
        return error(request, 'SERVICE_UNAVAILABLE', 503)
    return JsonResponse({'receipt_id': str(record.pk), 'message': '预约申请已提交'}, status=201)


def sales_access(user, permission='manage_consultation'):
    return bool(user and user.is_authenticated and user.is_active and user.is_staff and user.has_perm(
        'backend.' + permission))


def update_sales(record_id, *, actor, version, owner_id, status, notes, request_id=None):
    if not sales_access(actor):
        raise ValidationError('CONSULTATION_FORBIDDEN')
    if status not in ('pending', 'in_progress', 'completed') or not isinstance(notes, str) or len(notes) > 4000:
        raise ValidationError('CONSULTATION_INVALID_UPDATE')
    with transaction.atomic():
        record = Consultation.objects.select_for_update().get(pk=record_id)
        if record.version != version:
            raise ValidationError('CONSULTATION_EDIT_CONFLICT')
        if record.purged_at:
            raise ValidationError('CONSULTATION_ALREADY_PURGED')
        if owner_id:
            from django.contrib.auth import get_user_model
            owner = get_user_model().objects.filter(pk=owner_id).first()
            if not sales_access(owner):
                raise ValidationError('CONSULTATION_INVALID_OWNER')
        now = timezone.now()
        if status == 'completed' and record.status != 'completed':
            record.completed_at = now
            record.purge_at = now + timedelta(days=90)
        elif status != 'completed':
            record.completed_at = record.purge_at = None
        record.owner_id, record.status, record.notes = owner_id, status, notes
        record.version += 1
        record.save()
        ConsultationAudit.objects.create(consultation=record, actor=actor, action='sales_updated',
                                         request_id=request_id or uuid.uuid4())
        return record


def purge_record(record_id, *, actor=None, scheduled=False, now=None):
    if not scheduled and not sales_access(actor, 'purge_consultation'):
        raise ValidationError('CONSULTATION_FORBIDDEN')
    now = now or timezone.now()
    with transaction.atomic():
        record = Consultation.objects.select_for_update().get(pk=record_id)
        if record.purged_at:
            return False
        if scheduled and (record.status != 'completed' or not record.purge_at or record.purge_at > now):
            return False
        for field in ('company', 'phone', 'email', 'question', 'notes', 'request_hash'):
            setattr(record, field, '')
        record.purged_at = now
        record.version += 1
        record.save()
        ConsultationAudit.objects.create(consultation=record, actor=actor,
            action='retention_purged' if scheduled else 'early_purged', request_id=uuid.uuid4())
        return True
