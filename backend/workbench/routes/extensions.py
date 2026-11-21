"""Extensions catalog, installs and connector connections (35-36, 43-46, 73 屏)."""
from django.db import IntegrityError, transaction
from django.db.models import Count, Q

from .. import common
from ..models import Extension
from ..services import extensions as svc

TABS = ('installed', 'market')


def _tab_queryset(ctx, tab):
    if tab == 'installed':
        return svc.catalog(ctx).filter(installs__account=ctx.account)
    return Extension.objects.filter(account__isnull=True)


def list_extensions(request, ctx):
    params = common.query(request, ('tab', 'scope', 'kind', 'q', 'offset', 'installed'))
    if params.get('tab') and params.get('scope') and params['tab'] != params['scope']:
        raise ValueError('VALIDATION_FAILED')
    tab = params.get('tab') or params.get('scope') or 'installed'
    kind = params.get('kind')
    installed = params.get('installed')
    if installed not in (None, 'true', 'false'):
        raise ValueError('VALIDATION_FAILED')
    if tab not in TABS or (kind and kind not in svc.KINDS):
        raise ValueError('VALIDATION_FAILED')
    base = _tab_queryset(ctx, tab).select_related('required_connector')
    kind_counts = dict.fromkeys(svc.KINDS, 0)
    kind_counts.update(dict(base.values_list('kind').annotate(n=Count('id'))))
    qs = base
    if kind:
        qs = qs.filter(kind=kind)
    text = (params.get('q') or '').strip()
    if len(text) > 100:
        raise ValueError('VALIDATION_FAILED')
    if text:
        qs = qs.filter(Q(name__icontains=text) | Q(description__icontains=text))
    if installed == 'true':
        qs = qs.filter(installs__account=ctx.account)
    elif installed == 'false':
        # M2M 反向排除必须用子查询（exclude(installs__...) 会复用前面的 join）。
        qs = qs.exclude(pk__in=Extension.objects.filter(installs__account=ctx.account).values('pk'))
    qs = qs.order_by('installs__pk', 'pk') if tab == 'installed' else qs.order_by('pk')
    pre = svc.Preload(ctx)
    data = common.page(request, qs, lambda e: svc.extension_item(e, pre))
    data.update(tab=tab, counts={'installed': len(pre.installs)}, kind_counts=kind_counts)
    return data


def extension_detail(request, ctx, id):
    common.query(request)
    return svc.extension_detail(ctx, svc.get_extension(ctx, id))


def extension_versions(request, ctx, id):
    common.query(request, ('offset',))
    extension = svc.get_extension(ctx, id)
    # No version-history table exists yet (Q-E-12); the current catalog version is the only entry.
    rows = [{'version': extension.latest_version, 'released_at': extension.updated_at, 'notes': None,
             'permission_changes': []}]
    return common.page(request, rows, lambda row: row)


def import_extension(request, ctx):
    data = common.json_body(request, ('manifest',))
    with transaction.atomic():
        extension, install = svc.import_manifest(ctx, data['manifest'])
    return common.respond({'extension': svc.extension_detail(ctx, extension), 'install': svc.install_dict(install)},
                          201)


def install_extension(request, ctx, id):
    common.json_body(request, ())
    extension = svc.get_extension(ctx, id)
    try:
        with transaction.atomic():
            install = svc.install(ctx, extension)
    except IntegrityError as exc:
        raise ValueError('STATE_CONFLICT') from exc
    return common.respond({'install': svc.install_dict(install)}, 201)


def _toggle(request, ctx, id, enabled):
    common.json_body(request, ())
    extension = svc.get_extension(ctx, id)
    with transaction.atomic():
        install = svc.set_enabled(ctx, extension, enabled)
    return {'install': svc.install_dict(install)}


def enable_extension(request, ctx, id):
    return _toggle(request, ctx, id, True)


def disable_extension(request, ctx, id):
    return _toggle(request, ctx, id, False)


def _connection(ctx, public_id):
    row = svc.visible_connections(ctx).select_related('extension').filter(public_id=public_id).first()
    if row is None:
        raise ValueError('NOT_FOUND')
    return row


def connect(request, ctx, id):
    common.json_body(request, ())
    with transaction.atomic():
        return svc.connect(ctx, _connection(ctx, id))


def disconnect(request, ctx, id):
    common.json_body(request, ())
    with transaction.atomic():
        return svc.disconnect(ctx, _connection(ctx, id))


ROUTES = [
    ('GET', 'extensions', list_extensions),
    ('POST', 'extensions/import', import_extension),
    ('GET', 'extensions/{id}', extension_detail),
    ('GET', 'extensions/{id}/versions', extension_versions),
    ('POST', 'extensions/{id}/install', install_extension),
    ('POST', 'extensions/{id}/enable', enable_extension),
    ('POST', 'extensions/{id}/disable', disable_extension),
    ('POST', 'connections/{id}/connect', connect),
    ('POST', 'connections/{id}/disconnect', disconnect),
]
