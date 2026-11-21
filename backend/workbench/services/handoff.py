"""One-time website hand-off (D31, 65 屏): the App asks for a short-lived credential bound to the signed-in user
and the active ledger; the website redeems it once (GET auth/handoff, dispatched from views._api_request) to sign
the browser in as that user with that ledger selected, so 加购 needs no second sign-in or ledger choice.

Only the SHA-256 digest of the token is stored. Every failure redirects to the same page marked handoff=invalid, so
the website falls back to its normal sign-in without learning why.
"""
import hashlib
import re
import secrets
from datetime import timedelta

from django.contrib.auth import login
from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from .. import clock

from backend.models import AuditEvent, Membership

from ..common import iso, json_body, respond
from ..models import BillingHandoff
from .workspaces import rate_limited

TTL_SECONDS = 120
REDEEM_PATH = '/api/auth/handoff'
TARGETS = {'topup': ('/pages/billing.html', 'credits')}
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue(request, ctx):
    data = json_body(request, ('purpose',))
    if data['purpose'] not in TARGETS:
        raise ValueError('VALIDATION_FAILED')
    limited = rate_limited(request, ctx, 'billing-handoff')
    if limited:
        return limited
    token = secrets.token_urlsafe(32)
    row = BillingHandoff.objects.create(account=ctx.account, user=ctx.user, purpose=data['purpose'],
                                        token_digest=digest(token),
                                        expires_at=clock.now() + timedelta(seconds=TTL_SECONDS))
    return respond({'token': token, 'purpose': row.purpose, 'expires_at': iso(row.expires_at),
                    'expires_in': TTL_SECONDS, 'redeem_path': '%s?token=%s' % (REDEEM_PATH, token),
                    'account_id': str(row.account_id)}, 201)


def _redirect(purpose, ok):
    path, fragment = TARGETS.get(purpose, TARGETS['topup'])
    response = HttpResponseRedirect('%s%s#%s' % (path, '' if ok else '?handoff=invalid', fragment))
    # The token is in the request URL: keep it out of caches and of the Referer sent by the next page.
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def redeem(request):
    from backend.account import register_session
    keys = set(request.GET)
    token = request.GET.get('token', '')
    if keys != {'token'} or len(request.GET.getlist('token')) != 1 or not TOKEN.fullmatch(token):
        return _redirect(None, False)
    with transaction.atomic():
        row = BillingHandoff.objects.select_for_update().select_related('user').filter(
            token_digest=digest(token)).first()
        if row is None or row.consumed_at is not None:
            return _redirect(None, False)
        now = clock.now()
        # Burned on the first attempt whatever the outcome, so a leaked link cannot be retried.
        row.consumed_at = now
        row.save(update_fields=['consumed_at', 'updated_at'])
        user = row.user
        if (row.expires_at <= now or not user.is_active
                or not Membership.objects.filter(user=user, account_id=row.account_id, is_active=True).exists()
                or (request.user.is_authenticated and request.user.pk != user.pk)):
            return _redirect(row.purpose, False)
        if not request.user.is_authenticated:
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            register_session(request)
        request.session['active_account_id'] = str(row.account_id)
        AuditEvent.objects.create(actor=user, account_id=row.account_id, action='auth.handoff_redeemed',
                                  request_id=request.request_id, target_type='billing_handoff',
                                  target_id=row.public_id)
    return _redirect(row.purpose, True)
