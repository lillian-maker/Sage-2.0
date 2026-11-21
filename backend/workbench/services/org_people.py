"""Display names and user ids for 企业管理 people (D41): side table over commerce invitations and memberships."""
import uuid

from backend.models import LoginIdentity

from ..models import OrgPersonProfile
from . import billing

NAME_MAX = 80


def _row_invitation(profile):
    return {'invitation_id': str(profile.invitation_id), 'display_name': profile.display_name,
            'user_id': profile.user_id}


def _row_member(profile):
    return {'membership_id': profile.membership_id, 'display_name': profile.display_name,
            'user_id': profile.user_id}


def _require_manager(ctx):
    billing.require_business(ctx)
    billing.require_role(ctx, 'owner', 'billing_admin')


def people(ctx):
    _require_manager(ctx)
    rows = OrgPersonProfile.objects.filter(account=ctx.account).order_by('id')
    return {'me': {'membership_id': ctx.member.pk, 'user_id': ctx.user.pk},
            'invitations': [_row_invitation(p) for p in rows if p.invitation_id is not None],
            'members': [_row_member(p) for p in rows if p.membership_id is not None]}


def _invitation(ctx, invitation_id):
    from backend.commerce.models import BusinessInvitation
    try:
        key = uuid.UUID(str(invitation_id))
    except ValueError as exc:
        raise ValueError('NOT_FOUND') from exc
    row = BusinessInvitation.objects.filter(account=ctx.account, pk=key).first()
    if row is None:
        raise ValueError('NOT_FOUND')
    return row


def _invitee_user(invitation):
    match = LoginIdentity.objects.filter(method=invitation.method, identifier=invitation.identifier).first()
    if match is None and invitation.method == 'email':
        match = LoginIdentity.objects.filter(method='google', provider_email=invitation.identifier).first()
    return match.user_id if match else None


def record_invitation(account, invitation, display_name):
    profile, _ = OrgPersonProfile.objects.update_or_create(
        invitation_id=invitation.pk,
        defaults={'account': account, 'display_name': display_name, 'user_id': _invitee_user(invitation)})
    return profile


def set_invitation_profile(ctx, invitation_id, data):
    _require_manager(ctx)
    invitation = _invitation(ctx, invitation_id)
    name = data['display_name'].strip() if isinstance(data['display_name'], str) else ''
    if not 1 <= len(name) <= NAME_MAX:
        raise ValueError('VALIDATION_FAILED')
    return _row_invitation(record_invitation(ctx.account, invitation, name))


def record_member(membership):
    """Called from workbench member flows; keeps the membership's user id and current full name."""
    profile, _ = OrgPersonProfile.objects.update_or_create(
        membership=membership,
        defaults={'account_id': membership.account_id, 'user_id': membership.user_id,
                  'display_name': billing.display_name(membership.user)[:NAME_MAX]})
    return profile
