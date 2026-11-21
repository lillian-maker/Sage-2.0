"""Base sample world taken from the Figma screens (workspace members page 722:2, home 630)."""
from datetime import timedelta

from django.contrib.auth import get_user_model

from ._clock import timezone

from backend.models import AccountProfile, BillingAccount, LoginIdentity, Membership
from backend.workbench.models import Workspace, WorkspaceMember

ORDER = 0

USERS = (
    # key, email, first_name, last_name
    ('lillian', 'lillian@sagedemo.com', 'Lillian', 'Li'),
    ('chen', 'chen@sage.demo', 'Chen', 'Yu'),
    ('mark', 'mark@sage.demo', 'Mark', 'Weber'),
    ('anna', 'anna@partner.de', 'Anna', 'Koch'),
    # 企业管理成员页 (760:476) 设计稿的成员，seed 后可与 Wang Min 离职稿一起核对
    ('zhao', 'zhaohui@sage.demo', 'Zhao', 'Hui'),
    ('sun', 'suntao@sage.demo', 'Sun', 'Tao'),
)
BUSINESS_NAME = 'Sage Demo 企业'
# Figma only labels workspace roles; account roles beyond the owner are not shown.
BUSINESS_ROLES = (('lillian', 'owner'), ('chen', 'member'), ('mark', 'member'), ('zhao', 'member'), ('sun', 'member'))
WORKSPACE_NAME = '德国市场进入'
WORKSPACE_MEMBERS = (('lillian', 'owner'), ('chen', 'editor'), ('mark', 'viewer'))
GUEST_INVITE = ('anna', 'guest')
# Screen 18 "最近活跃": Chen 昨天, Mark 11月2日 (days before the seed clock's 11月19日).
LAST_ACTIVE_DAYS = (('chen', 1), ('mark', 17), ('zhao', 0), ('sun', 3))


# Databases seeded before the demo address changed keep the same user row instead of gaining a second Lillian.
LEGACY_EMAILS = {'lillian@sagedemo.com': 'lillian@example.com'}


def _user(email, first_name, last_name):
    identity = LoginIdentity.objects.filter(method='email', identifier=email).select_related('user').first()
    if identity:
        return identity.user
    legacy = LoginIdentity.objects.filter(method='email', identifier=LEGACY_EMAILS.get(email, '-')).select_related(
        'user').first()
    if legacy:
        user = legacy.user
        legacy.identifier = email
        legacy.save(update_fields=['identifier'])
        user.email = email
        fields = ['email']
        if user.username == LEGACY_EMAILS[email]:
            user.username = email
            fields.append('username')
        user.save(update_fields=fields)
        return user
    user = get_user_model().objects.create_user(username=email, email=email, first_name=first_name,
                                                last_name=last_name, password=None)
    LoginIdentity.objects.create(user=user, method='email', identifier=email)
    account = BillingAccount.objects.create(kind='personal', name='个人账户', personal_owner=user)
    Membership.objects.create(account=account, user=user, role='owner')
    return user


def seed(ctx):
    users = {key: _user(email, first, last) for key, email, first, last in USERS}
    AccountProfile.objects.get_or_create(user=users['lillian'], defaults={'assistant_name': 'Sage 助手'})
    business = BillingAccount.objects.filter(kind='business', name=BUSINESS_NAME).first()
    if business is None:
        business = BillingAccount.objects.create(kind='business', name=BUSINESS_NAME)
    memberships = {}
    for key, role in BUSINESS_ROLES:
        memberships[key], _ = Membership.objects.get_or_create(account=business, user=users[key],
                                                               defaults={'role': role})
    workspace, _ = Workspace.objects.get_or_create(
        account=business, name=WORKSPACE_NAME,
        defaults={'created_from': 'blank', 'created_by': users['lillian'],
                  'description': '评估家用小家电进入德国市场：竞争格局、合规、物流成本与渠道选择。'})
    for key, role in WORKSPACE_MEMBERS:
        WorkspaceMember.objects.get_or_create(
            account=business, workspace=workspace, user=users[key],
            defaults={'role': role, 'state': 'active', 'invited_by': users['lillian']})
    guest_key, guest_role = GUEST_INVITE
    WorkspaceMember.objects.get_or_create(
        account=business, workspace=workspace, invite_email=users[guest_key].email, state='invited',
        defaults={'role': guest_role, 'invited_by': users['lillian']})
    now = timezone.now()
    for key, days in LAST_ACTIVE_DAYS:
        get_user_model().objects.filter(pk=users[key].pk).update(last_login=now - timedelta(days=days))
    ctx.update(users=users, business=business, memberships=memberships, workspace=workspace)
