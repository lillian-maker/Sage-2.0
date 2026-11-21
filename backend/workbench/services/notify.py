"""Cross-domain side effects shared by every route module, so parallel domains do not each grow their own copy."""
from django.db import IntegrityError, transaction
from django.utils import timezone
from .. import clock

from ..models import AppNotification


def notify(*, account, recipient, category, type, text, dedupe_key, context_label='', target_type='',
           target_id='', action=None, actor_expert=None, occurred_at=None):
    """Create one in-app notification; a repeated dedupe_key for the same recipient is a no-op."""
    try:
        with transaction.atomic():
            return AppNotification.objects.create(
                account=account, recipient=recipient, category=category, type=type, text=text[:200],
                context_label=context_label[:80], target_type=target_type, target_id=target_id, action=action,
                actor_expert=actor_expert, occurred_at=occurred_at or clock.now(), dedupe_key=dedupe_key[:64])
    except IntegrityError:
        return AppNotification.objects.filter(recipient=recipient, dedupe_key=dedupe_key[:64]).first()
