"""Org member list (75 / 78): last activity per member.

The member rows read last activity from AccountSession; these rows are already revoked, so they only carry a
last-seen time and never count as signed-in devices. Runs after handover (Wang Min)."""
import secrets
from datetime import timedelta

from ._clock import timezone

from backend.models import AccountSession, LoginIdentity

ORDER = 101
# 758 / 760: Chen Yu 1 小时前, Wang Min 昨天, Sun Tao 3 天前 (minutes before the seed clock).
LAST_SEEN_MINUTES = (('chen@sage.demo', 60), ('wangmin@sage.demo', 24 * 60), ('suntao@sage.demo', 3 * 24 * 60))


def seed(ctx):
    _seat_for_wang_min()
    now = timezone.now()
    for email, minutes in LAST_SEEN_MINUTES:
        identity = LoginIdentity.objects.filter(method='email', identifier=email).select_related('user').first()
        if identity is None or AccountSession.objects.filter(user=identity.user, client='seed:last-seen').exists():
            continue
        row = AccountSession.objects.create(user=identity.user, session_key=secrets.token_hex(16), client='seed:last-seen',
                                            revoked_at=now - timedelta(minutes=minutes),
                                            last_seen_at=now - timedelta(minutes=minutes))
        AccountSession.objects.filter(pk=row.pk).update(created_at=now - timedelta(minutes=minutes, days=7))

def _seat_for_wang_min():
    """758 / 760: Wang Min holds one of the enterprise seats (4 assigned of 10)."""
    from backend.commerce.models import BusinessInvitation, PaidSeat
    from backend.models import Membership
    member = Membership.objects.filter(user__email='wangmin@sage.demo', account__kind='business', is_active=True).first()
    if member is None or PaidSeat.objects.filter(membership=member).exists():
        return
    reserved = BusinessInvitation.objects.filter(state='pending', seat__isnull=False).values('seat_id')
    seat = (PaidSeat.objects.filter(subscription__account=member.account, membership__isnull=True)
            .exclude(pk__in=reserved).order_by('position').first())
    if seat is not None:
        seat.membership = member
        seat.save(update_fields=['membership'])
