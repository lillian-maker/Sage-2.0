"""Trusted list validation and atomic new-user attribution for gated signup."""
import csv
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .http import client_ip, error
from .models import InvitationCode, RegistrationAttribution


def normalize_code(value):
    if not isinstance(value, str):
        raise ValueError('INVALID_INVITATION_CODE')
    value = value.strip()
    if value and not re.fullmatch(r'[A-Za-z0-9]{6,16}', value):
        raise ValueError('INVALID_INVITATION_CODE')
    return value.upper()


def validate_code(value, *, lock=False):
    code = normalize_code(value)
    if not code:
        return None
    if not settings.INVITATION_CODES_READY:
        raise ValueError('INVITATION_CODES_UNAVAILABLE')
    query = InvitationCode.objects.filter(pk=code)
    if lock:
        query = query.select_for_update()
    record = query.first()
    if record is None or not record.is_active:
        raise ValueError('INVALID_INVITATION_CODE')
    return record


@sensitive_variables()
@transaction.atomic
def create_registered_user(*, invitation_code='', expires_at=None, **user_fields):
    """Trusted signup calls this only after its password/CAPTCHA/contact gates.

    Never accepts an existing user. The final code lock, user creation, and
    attribution commit together. This function does not itself grant auth proof.
    """
    user_model = get_user_model()
    if {'pk', user_model._meta.pk.name, user_model._meta.pk.attname}.intersection(user_fields):
        raise ValueError('INVALID_REGISTRATION_FIELDS')
    code = validate_code(invitation_code, lock=True)
    if expires_at is not None and expires_at <= timezone.now():
        raise ValueError('INVALID_FLOW')
    user = user_model.objects.create_user(**user_fields)
    if code:
        RegistrationAttribution.objects.create(user=user, invitation_code=code, registered_at=user.date_joined)
    return user


@sensitive_variables()
def check_request(request):
    from .views import payload, throttle
    if request.GET:
        raise ValueError('INVALID_REQUEST')
    data = payload(request, ('invitation_code',))
    with transaction.atomic():
        if not throttle('invitation-ip:' + client_ip(request), settings.AUTH_IP_LIMIT):
            return error(request, 'RATE_LIMITED', 429, '请求较频繁，请稍后重试。')
    validate_code(data['invitation_code'])
    # This response neither identifies the blogger nor grants signup authority.
    return JsonResponse({'valid': True})


def read_invitation_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, None) != ['邀请码', '博主', '状态']:
            raise ValueError('INVALID_INVITATION_CSV')
        rows, seen = [], set()
        for row in reader:
            if len(row) != 3:
                raise ValueError('INVALID_INVITATION_CSV')
            code = normalize_code(row[0])
            blogger, state = row[1].strip(), row[2].strip()
            if (not code or code in seen or not blogger or len(blogger) > 160 or
                    any(ord(char) < 32 for char in blogger) or state not in ('启用', '停用')):
                raise ValueError('INVALID_INVITATION_CSV')
            seen.add(code)
            rows.append((code, blogger, state == '启用'))
        return sorted(rows)


@transaction.atomic
def import_codes(rows, *, apply=False):
    # Lock existing rows in a stable order and prevalidate the whole batch.
    existing = {record.code: record for record in InvitationCode.objects.select_for_update().filter(
        code__in=[row[0] for row in rows]).order_by('code')}
    for code, blogger, _ in rows:
        if code in existing and existing[code].blogger != blogger:
            raise ValueError('IMMUTABLE_INVITATION_MAPPING')
    created = sum(code not in existing for code, _, _ in rows)
    updated = sum(code in existing and existing[code].is_active != active for code, _, active in rows)
    if apply:
        for code, blogger, active in rows:
            record = existing.get(code)
            if record is None:
                InvitationCode.objects.create(code=code, blogger=blogger, is_active=active)
            elif record.is_active != active:
                record.is_active = active
                record.save(update_fields=['is_active'])
    return {'mode': 'apply' if apply else 'dry-run', 'rows': len(rows), 'created': created, 'updated': updated}
