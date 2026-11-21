"""Knowledge bases, documents and the knowledge graph (39-42 屏)."""
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.http import HttpResponse
from django.http.multipartparser import MultiPartParserError
from django.utils.http import content_disposition_header

from .. import common
from ..ids import PUBLIC_ID_PATTERN
from ..models import Task, TaskMessage
from ..models import Task, TaskMessage
from ..services import knowledge as svc
from ..services import workspaces as ws

SORTS = ('updated', 'name', 'cited')


def _search(params):
    text = (params.get('q') or '').strip()
    if len(text) > 100:
        raise ValueError('VALIDATION_FAILED')
    return text


def _bool(value):
    if value in (None, '', 'false'):
        return False
    if value == 'true':
        return True
    raise ValueError('VALIDATION_FAILED')


def _version(data):
    value = data.get('version')
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError('VALIDATION_FAILED')
    return value


# Knowledge bases ---------------------------------------------------------------

def list_bases(request, ctx):
    params = common.query(request, ('scope', 'citable', 'q', 'offset'))
    scope = params.get('scope') or 'all'
    if scope not in ('all',) + svc.SCOPE_ORDER:
        raise ValueError('VALIDATION_FAILED')
    qs = svc.citable_bases(ctx) if _bool(params.get('citable')) else svc.visible_bases(ctx)
    if scope != 'all':
        qs = qs.filter(scope=scope)
    text = _search(params)
    if text:
        qs = qs.filter(name__icontains=text)
    bases = svc.sorted_bases(qs.select_related('workspace', 'editor_department'))
    stats = svc.base_stats(bases)
    data = common.page(request, bases, lambda kb: svc.base_item(ctx, kb, stats))
    data['index_summary'] = svc.index_summary(ctx, svc.visible_bases(ctx))
    return data


def base_detail(request, ctx, id):
    common.query(request)
    return svc.base_detail(ctx, svc.get_base(ctx, id))


def update_base(request, ctx, id):
    data = common.json_body(request, ('version',),
                            ('cite_audience', 'editor_ids', 'editor_department_id', 'expert_cite_enabled'))
    _version(data)
    with transaction.atomic():
        kb = svc.get_base(ctx, id)
        kb = svc.KnowledgeBase.objects.select_for_update().select_related('workspace').get(pk=kb.pk)
        svc.update_base(request, ctx, kb, data)
    return {'knowledge_base': svc.base_detail(ctx, svc.get_base(ctx, id))}


def base_audit(request, ctx, id):
    common.query(request, ('offset',))
    kb = svc.get_base(ctx, id)
    if not svc.can_edit(ctx, kb):
        raise ValueError('PERMISSION_DENIED')
    return common.page(request, svc.audit_items(ctx, kb), svc.audit_dict)


# Documents ------------------------------------------------------------------------

def list_documents(request, ctx):
    params = common.query(request, ('knowledge_base', 'workspace', 'q', 'sort', 'offset', 'task'))
    sort = params.get('sort') or 'updated'
    if sort not in SORTS:
        raise ValueError('VALIDATION_FAILED')
    qs = svc.visible_documents(ctx)
    if params.get('knowledge_base'):
        qs = qs.filter(knowledge_base=svc.get_base(ctx, params['knowledge_base']))
    if params.get('workspace'):
        workspace, _ = ws.get_workspace(ctx, params['workspace'])
        qs = qs.filter(knowledge_base__workspace=workspace)
    text = _search(params)
    if text:
        qs = qs.filter(title__icontains=text)
    if params.get('task'):
        task = Task.objects.filter(account=ctx.account, public_id=params['task']).first()
        if task is None or ws.task_access(ctx, task) is None:
            raise ValueError('NOT_FOUND')
        linked = TaskMessage.attached_documents.through.objects.filter(
            taskmessage__task=task).values('knowledgedocument_id')
        qs = qs.filter(pk__in=linked)
    docs = list(qs.select_related('uploaded_by', 'source_artifact_version__author_expert'))
    counts = svc.citation_counts(ctx, docs)
    if sort == 'name':
        docs.sort(key=lambda d: (d.title, d.pk))
    elif sort == 'cited':
        docs.sort(key=lambda d: (-counts.get(d.pk, 0), -d.updated_at.timestamp(), d.pk))
    else:
        docs.sort(key=lambda d: (-d.updated_at.timestamp(), -d.pk))
    return common.page(request, docs, lambda d: svc.document_item(d, counts))


def document_detail(request, ctx, id):
    common.query(request)
    return svc.document_detail(ctx, svc.get_document(ctx, id))


def document_page(request, ctx, id, n):
    common.query(request)
    doc = svc.get_document(ctx, id)
    if doc.index_state != 'indexed':
        raise ValueError('STATE_CONFLICT')
    pages = svc.read_document_content(doc).get('pages') or []
    number = int(n)
    if not 1 <= number <= len(pages):
        raise ValueError('NOT_FOUND')
    return {'page': number, 'page_count': doc.page_count or len(pages),
            'blocks': pages[number - 1].get('blocks', [])}


def document_download(request, ctx, id):
    common.query(request)
    doc = svc.get_document(ctx, id)
    body = svc.read_document_content(doc)
    # common.to_response only accepts HttpResponse, so the file is buffered rather than streamed.
    with svc.open_original(body) as handle:
        content = handle.read()
    response = HttpResponse(content, content_type=body.get('content_type') or 'application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(True, body.get('original_name') or doc.title)
    return response


def create_document(request, ctx):
    data = common.json_body(request, ('knowledge_base_id', 'source_kind', 'source_url', 'refresh_interval'),
                            ('title',))
    with transaction.atomic():
        doc = svc.create_web_document(ctx, data)
    return common.respond({'document': svc.document_detail(ctx, svc.get_document(ctx, doc.public_id))}, 201)



def upload_document(request, ctx):
    common.query(request)
    if request.content_type != 'multipart/form-data':
        raise ValueError('INVALID_REQUEST')
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError as exc:
        raise ValueError('INVALID_REQUEST') from exc
    if length > svc.upload_max_bytes() + common.DEFAULT_BODY_LIMIT:
        raise ValueError('INVALID_BODY_SIZE')
    try:
        fields, files = request.POST, request.FILES
    except (MultiPartParserError, SuspiciousOperation) as exc:
        raise ValueError('INVALID_REQUEST') from exc
    names = set(fields)
    if (set(files) != {'file'} or 'knowledge_base_id' not in names or names - {'knowledge_base_id', 'title'}
            or any(len(fields.getlist(k)) > 1 for k in names) or len(files.getlist('file')) > 1):
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        doc = svc.create_upload_document(ctx, fields['knowledge_base_id'], files['file'], fields.get('title'))
    return common.respond({'document': svc.document_detail(ctx, svc.get_document(ctx, doc.public_id))}, 201)


def reindex_document(request, ctx, id):
    common.json_body(request, ())
    with transaction.atomic():
        doc = svc.reindex(ctx, svc.get_document(ctx, id))
    return common.respond({'document': svc.document_detail(ctx, svc.get_document(ctx, doc.public_id))}, 202)


# Graph ----------------------------------------------------------------------------------

def list_entities(request, ctx):
    params = common.query(request, ('type', 'q', 'include_superseded', 'offset'))
    _bool(params.get('include_superseded'))
    kind = params.get('type')
    if kind and kind not in svc.ENTITY_TYPES:
        raise ValueError('VALIDATION_FAILED')
    base = svc.entities(ctx)
    type_counts = dict.fromkeys(svc.ENTITY_TYPES, 0)
    for entity_type in base.values_list('entity_type', flat=True):
        type_counts[entity_type] += 1
    qs = base.filter(entity_type=kind) if kind else base
    text = _search(params)
    if text:
        qs = qs.filter(name__icontains=text)
    conflicts = svc.conflict_entity_ids(ctx)
    data = common.page(request, qs.order_by('pk'), lambda e: svc.entity_item(e, conflicts))
    data['type_counts'] = type_counts
    return data


def _entity(ctx, public_id):
    entity = svc.entities(ctx).filter(public_id=public_id).first()
    if entity is None:
        raise ValueError('NOT_FOUND')
    return entity


def entity_detail(request, ctx, id):
    params = common.query(request, ('include_superseded',))
    return svc.entity_detail(ctx, _entity(ctx, id), _bool(params.get('include_superseded')))


def entity_neighborhood(request, ctx, id):
    common.query(request)
    return svc.neighborhood(ctx, _entity(ctx, id))


def resolve_conflict(request, ctx, id, key):
    data = common.json_body(request, ('preferred_fact_id',))
    if not isinstance(data['preferred_fact_id'], str):
        raise ValueError('VALIDATION_FAILED')
    with transaction.atomic():
        entity = _entity(ctx, id)
        svc.resolve_conflict(ctx, entity, key, data['preferred_fact_id'])
    return svc.entity_detail(ctx, entity, False)


ROUTES = [
    ('GET', 'knowledge-bases', list_bases),
    ('GET', 'knowledge-bases/{id}', base_detail),
    ('PATCH', 'knowledge-bases/{id}', update_base),
    ('GET', 'knowledge-bases/{id}/audit', base_audit),
    ('GET', 'documents', list_documents),
    ('POST', 'documents', create_document),
    ('POST', 'documents/upload', upload_document),
    ('GET', 'documents/{id}', document_detail),
    ('GET', r'^documents/(?P<id>%s)/pages/(?P<n>[1-9][0-9]{0,3})$' % PUBLIC_ID_PATTERN, document_page),
    ('GET', 'documents/{id}/download', document_download),
    ('POST', 'documents/{id}/reindex', reindex_document),
    ('GET', 'graph/entities', list_entities),
    ('GET', 'graph/entities/{id}', entity_detail),
    ('GET', 'graph/entities/{id}/neighborhood', entity_neighborhood),
    ('POST', r'^graph/entities/(?P<id>%s)/conflicts/(?P<key>[a-z0-9_]{1,64})/resolve$' % PUBLIC_ID_PATTERN,
     resolve_conflict),
]
