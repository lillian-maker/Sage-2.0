"""Extension catalog, account-level installs (Q-P14 A), connector connections and policy gates (E38-E40)."""
import os

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from .. import clock
from django.utils.module_loading import import_string

from ..models import (AccountPolicy, ConnectorConnection, ExpertCapability, Extension, ExtensionAllowEntry,
                      ExtensionInstall, InboxSource, TaskStep)
from . import workspaces as ws

KINDS = ('plugin', 'skill', 'connector', 'agent')
ADMIN_ROLES = ('owner', 'billing_admin')
# "安装第三方扩展" in AccountPolicy.approval_required_actions covers everything that is not official/enterprise.
THIRD_PARTY_TIERS = ('third_party', 'community_reviewed', 'local_import')
APPROVAL_ACTION = 'install_third_party_extension'
COMMON_NEXT_LIMIT = 3


FAKE_ADAPTER_ENVIRONMENTS = ('development', 'test')


def adapter(setting_name):
    """Replaceable integration point: settings.<NAME> is a dotted path or a callable; None means unavailable.

    A configured setting always wins. Without one, SAGE_WORKBENCH_FAKE_ADAPTERS=1 in development/test returns the
    deterministic stand-in from fake_adapters; production never does, whatever the variable says.
    """
    value = getattr(settings, setting_name, None)
    if isinstance(value, str) and value:
        return import_string(value)
    if callable(value):
        return value
    if os.environ.get('SAGE_WORKBENCH_FAKE_ADAPTERS') == '1' and settings.ENVIRONMENT in FAKE_ADAPTER_ENVIRONMENTS:
        from .fake_adapters import ADAPTERS
        return ADAPTERS.get(setting_name)
    return None


def catalog(ctx):
    return Extension.objects.filter(Q(account__isnull=True) | Q(account=ctx.account))


def get_extension(ctx, public_id):
    extension = catalog(ctx).select_related('required_connector').filter(public_id=public_id).first()
    if extension is None:
        raise ValueError('NOT_FOUND')
    return extension


def install_state(install):
    if install is None:
        return 'not_installed'
    if install.pending_permission_review:
        return 'pending_review'
    return 'installed' if install.enabled else 'disabled'


def visible_connections(ctx):
    return ConnectorConnection.objects.filter(account=ctx.account).filter(
        Q(owner_user=ctx.user) | Q(owner_user__isnull=True))


def connection_map(ctx):
    """extension pk -> the connection the current user acts with (own connection wins over the account one)."""
    result = {}
    for row in visible_connections(ctx).order_by('pk'):
        current = result.get(row.extension_id)
        if current is None or (current.owner_user_id is None and row.owner_user_id is not None):
            result[row.extension_id] = row
    return result


def policy(account):
    return AccountPolicy.objects.filter(account=account).first()


def is_allowed(account, extension, pol):
    if extension.account_id and extension.account_id != account.pk:
        return False
    if pol is None or (extension.pk and ExtensionAllowEntry.objects.filter(account=account, extension=extension).exists()):
        return True
    if pol.extension_allowlist_kinds and extension.kind not in pol.extension_allowlist_kinds:
        return False
    if pol.extension_allowed_tiers and extension.publisher_tier not in pol.extension_allowed_tiers:
        return False
    return True


def needs_approval(ctx, extension, pol):
    return bool(pol and APPROVAL_ACTION in (pol.approval_required_actions or [])
                and extension.publisher_tier in THIRD_PARTY_TIERS and ctx.member.role != 'owner')


def check_install_policy(ctx, extension):
    pol = policy(ctx.account)
    if not is_allowed(ctx.account, extension, pol):
        raise ValueError('EXTENSION_NOT_ALLOWED')
    # Enterprise integrations are switched on by account administrators ("需管理员开通").
    if extension.publisher_tier == 'enterprise' and ctx.member.role not in ADMIN_ROLES:
        raise ValueError('PERMISSION_DENIED')
    if needs_approval(ctx, extension, pol):
        raise ValueError('POLICY_APPROVAL_REQUIRED')


def counted_experts(ctx):
    """Experts behind "用于 N 位专家" and used_by_experts: drafts are not counted (D38)."""
    from .experts import visible_experts
    return visible_experts(ctx).exclude(status='draft')


class Preload:
    """Per-request lookups shared by list and detail serializers."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.installs = {row.extension_id: row for row in ExtensionInstall.objects.filter(account=ctx.account)}
        self.connections = connection_map(ctx)
        self.usage = dict(ExpertCapability.objects.filter(
            account=ctx.account, expert__in=counted_experts(ctx)).values_list('extension_id').annotate(n=Count('id')))
        self.inbox = set(InboxSource.objects.filter(account=ctx.account, disconnected_at__isnull=True)
                         .values_list('connection__extension_id', flat=True))

    def connection_state(self, extension):
        row = self.connections.get(extension.pk)
        if row is not None:
            return row.state
        if extension.publisher_tier == 'enterprise' and extension.pk not in self.installs:
            return 'admin_required'
        return 'not_connected'


def connection_dict(row):
    if row is None:
        return None
    return {'id': row.public_id, 'extension_id': row.extension.public_id, 'state': row.state,
            'connected_at': row.connected_at, 'last_error_code': row.last_error_code}


def install_dict(row):
    return {'id': row.public_id, 'extension_id': row.extension.public_id, 'installed_version': row.installed_version,
            'enabled': row.enabled, 'install_state': install_state(row),
            'pending_permission_review': row.pending_permission_review, 'installed_at': row.created_at}


def extension_item(extension, pre):
    install = pre.installs.get(extension.pk)
    connection = pre.connections.get(extension.pk) if extension.kind == 'connector' else None
    required = extension.required_connector
    required_state = pre.connection_state(required) if required else None
    return {
        'id': extension.public_id, 'kind': extension.kind, 'name': extension.name,
        'description': extension.description, 'publisher_tier': extension.publisher_tier,
        'access': extension.access, 'billing_note': extension.billing_note,
        'installed_version': install.installed_version if install else None,
        'latest_version': extension.latest_version, 'install_state': install_state(install),
        'enabled': bool(install and install.enabled),
        'used_by_expert_count': pre.usage.get(extension.pk, 0), 'used_by_inbox': extension.pk in pre.inbox,
        'global_usage_count': extension.global_usage_count,
        'connection': {'id': connection.public_id, 'state': connection.state} if connection else None,
        'required_connector': ({'id': required.public_id, 'name': required.name, 'connection_state': required_state}
                               if required else None),
        'missing_connection': bool(required) and required_state != 'connected',
    }


def common_next(ctx, extension, pre):
    """Share of tasks using this skill whose later steps use another skill (45 屏 "68% 的任务接着使用")."""
    rows = TaskStep.objects.filter(task__in=ws.visible_tasks(ctx), skill__isnull=False).values_list(
        'task_id', 'order', 'skill_id')
    by_task = {}
    for task_id, order, skill_id in rows:
        by_task.setdefault(task_id, []).append((order, skill_id))
    used, follow = 0, {}
    for steps in by_task.values():
        orders = [order for order, skill_id in steps if skill_id == extension.pk]
        if not orders:
            continue
        used += 1
        first = min(orders)
        for skill_id in {skill_id for order, skill_id in steps if order > first and skill_id != extension.pk}:
            follow[skill_id] = follow.get(skill_id, 0) + 1
    if not used:
        return []
    ranked = sorted(follow.items(), key=lambda item: (-item[1], item[0]))[:COMMON_NEXT_LIMIT]
    targets = Extension.objects.select_related('required_connector').in_bulk([pk for pk, _ in ranked])
    result = []
    for pk, count in ranked:
        target = targets[pk]
        required = target.required_connector
        result.append({'extension_id': target.public_id, 'name': target.name, 'ratio': round(count / used, 2),
                       'needs_connection': bool(required) and pre.connection_state(required) != 'connected'})
    return result


def extension_detail(ctx, extension, pre=None):
    pre = pre or Preload(ctx)
    data = extension_item(extension, pre)
    required = extension.required_connector
    experts = counted_experts(ctx).filter(
        pk__in=ExpertCapability.objects.filter(account=ctx.account, extension=extension).values('expert_id')
    ).order_by('catalog_code', 'name')
    data.update(
        permissions=extension.permissions, prerequisite_inputs=extension.prerequisite_inputs,
        outputs=extension.outputs, acceptance_template=extension.acceptance_template,
        common_next=common_next(ctx, extension, pre),
        used_by_experts=[{'id': e.public_id, 'name': e.name, 'avatar_ref': ws.avatar_url(e.avatar_ref)} for e in experts],
        install=install_dict(pre.installs[extension.pk]) if extension.pk in pre.installs else None,
    )
    if required:
        data['required_connector'].update(connection=connection_dict(pre.connections.get(required.pk)))
    return data


def install(ctx, extension):
    if ExtensionInstall.objects.filter(account=ctx.account, extension=extension).exists():
        raise ValueError('STATE_CONFLICT')
    check_install_policy(ctx, extension)
    row = ExtensionInstall.objects.create(account=ctx.account, extension=extension,
                                          installed_version=extension.latest_version, enabled=True,
                                          installed_by=ctx.user)
    if extension.kind == 'connector' and not ConnectorConnection.objects.filter(
            account=ctx.account, extension=extension, owner_user__isnull=True).exists():
        ConnectorConnection.objects.create(account=ctx.account, extension=extension, state='not_connected')
    return row


def set_enabled(ctx, extension, enabled):
    row = ExtensionInstall.objects.filter(account=ctx.account, extension=extension).first()
    if row is None:
        raise ValueError('STATE_CONFLICT')
    if ctx.member.role not in ADMIN_ROLES and row.installed_by_id != ctx.user.pk:
        raise ValueError('PERMISSION_DENIED')
    if row.enabled == enabled or row.pending_permission_review:
        raise ValueError('STATE_CONFLICT')
    row.enabled = enabled
    row.save(update_fields=['enabled', 'updated_at'])
    return row


def connect(ctx, connection):
    if connection.state == 'connected' or (connection.state == 'disabled'):
        raise ValueError('STATE_CONFLICT')
    pol = policy(ctx.account)
    if not is_allowed(ctx.account, connection.extension, pol):
        raise ValueError('EXTENSION_NOT_ALLOWED')
    if needs_approval(ctx, connection.extension, pol) and not ExtensionInstall.objects.filter(
            account=ctx.account, extension=connection.extension).exists():
        raise ValueError('POLICY_APPROVAL_REQUIRED')
    authorizer = adapter('WORKBENCH_CONNECTOR_AUTHORIZER')
    if authorizer is None:
        raise ValueError('SOURCE_UNAVAILABLE')
    try:
        result = authorizer(connection=connection, user=ctx.user) or {}
    except ConnectionError as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc
    if result.get('authorize_url'):
        return {'authorize_url': result['authorize_url'], 'connection': connection_dict(connection)}
    if not result.get('credential_ref'):
        raise ValueError('SOURCE_UNAVAILABLE')
    connection.state = 'connected'
    connection.credential_ref = result['credential_ref']
    connection.connected_by = ctx.user
    connection.connected_at = clock.now()
    connection.last_error_code = None
    connection.revoked_reason = None
    connection.save()
    return {'connection': connection_dict(connection)}


def disconnect(ctx, connection):
    if connection.state not in ('connected', 'expired'):
        raise ValueError('STATE_CONFLICT')
    connection.state = 'not_connected'
    connection.credential_ref = None
    connection.connected_at = None
    connection.save()
    return {'connection': connection_dict(connection)}


def import_manifest(ctx, manifest):
    """Local skill import (C-E-3). The upload protocol is pending Q-D1, so the manifest arrives as JSON."""
    if not isinstance(manifest, dict) or set(manifest) - {
            'kind', 'name', 'description', 'version', 'permissions', 'prerequisite_inputs', 'outputs',
            'acceptance_template'}:
        raise ValueError('VALIDATION_FAILED')
    kind = manifest.get('kind', 'skill')
    if kind != 'skill':
        raise ValueError('VALIDATION_FAILED')
    name = ws.text_field(manifest.get('name'), 60)
    description = ws.text_field(manifest.get('description', ''), 200, required=False) or ''
    version = ws.text_field(manifest.get('version'), 16)
    lists = {}
    for key in ('permissions', 'prerequisite_inputs', 'outputs', 'acceptance_template'):
        value = manifest.get(key, [])
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError('VALIDATION_FAILED')
        lists[key] = value
    if catalog(ctx).filter(kind=kind, name=name).exists():
        raise ValueError('VALIDATION_FAILED')
    probe = Extension(account=ctx.account, kind=kind, name=name, publisher_tier='local_import')
    check_install_policy(ctx, probe)
    extension = Extension.objects.create(account=ctx.account, kind=kind, name=name, description=description,
                                         publisher_tier='local_import', latest_version=version, **lists)
    row = ExtensionInstall.objects.create(account=ctx.account, extension=extension, installed_version=version,
                                          enabled=True, installed_by=ctx.user)
    return extension, row
