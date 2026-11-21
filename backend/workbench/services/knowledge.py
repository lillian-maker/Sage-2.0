"""Knowledge bases, documents, citation counts and the knowledge graph (E41-E46)."""
import json
import mimetypes
import re
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db.models import Count, Max, Q
from django.utils import timezone
from .. import clock

from backend.commerce.models import Department, DepartmentMember
from backend.models import Membership

from ..models import Citation, KnowledgeBase, KnowledgeDocument, KnowledgeEntity, KnowledgeFact, KnowledgeRelation
from . import artifacts as artifact_service
from . import workspaces as ws

SCOPE_ORDER = ('personal', 'workspace', 'account')
STALE_THRESHOLD_DAYS = 90
PROCESSING = ('queued', 'indexing')
REFRESH_INTERVALS = ('daily', 'weekly', 'monthly')
CITE_AUDIENCE_BY_SCOPE = {'personal': ('owner_only',), 'workspace': ('owner_only', 'workspace_all'),
                          'account': ('owner_only', 'account_all')}
ENTITY_TYPES = ('market', 'competitor', 'regulation', 'product', 'channel', 'task', 'time')


# Knowledge bases -----------------------------------------------------------------

def department_of(ctx, value):
    try:
        key = uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None
    return Department.objects.filter(account=ctx.account, pk=key).first()


def visible_bases(ctx):
    workspace_ids = ws.member_rows(ctx).exclude(role='guest').values('workspace_id')
    return KnowledgeBase.objects.filter(account=ctx.account).filter(
        Q(scope='personal', owner=ctx.user) | Q(scope='workspace', workspace_id__in=workspace_ids)
        | Q(scope='account'))


def citable_bases(ctx):
    """Bases the user may let an expert cite (37 屏 "只列出你有引用权限的知识库")."""
    return visible_bases(ctx).filter(expert_cite_enabled=True).filter(
        Q(cite_audience='owner_only', owner=ctx.user) | Q(cite_audience='workspace_all', scope='workspace')
        | Q(cite_audience='account_all'))


def get_base(ctx, public_id):
    kb = visible_bases(ctx).select_related('workspace', 'editor_department').filter(public_id=public_id).first()
    if kb is None:
        raise ValueError('NOT_FOUND')
    return kb


def can_edit(ctx, kb):
    if kb.owner_id == ctx.user.pk or ctx.member.role == 'owner':
        return True
    if kb.editors.filter(pk=ctx.user.pk).exists():
        return True
    if kb.editor_department_id and DepartmentMember.objects.filter(
            department_id=kb.editor_department_id, membership=ctx.member, ended_period__isnull=True).exists():
        return True
    if kb.scope == 'workspace':
        member = ws.my_member(ctx, kb.workspace)
        return bool(member and member.role == 'owner')
    return False


def live_documents():
    return KnowledgeDocument.objects.filter(deleted_at__isnull=True)


def base_stats(bases):
    ids = [kb.pk for kb in bases]
    stats = {pk: {'document_count': 0, 'processing_count': 0, 'failed_count': 0, 'failed_document_ids': [],
                  'content_updated_at': None} for pk in ids}
    for doc in live_documents().filter(knowledge_base_id__in=ids).only(
            'public_id', 'knowledge_base_id', 'index_state', 'updated_at').order_by('pk'):
        row = stats[doc.knowledge_base_id]
        row['document_count'] += 1
        if doc.index_state in PROCESSING:
            row['processing_count'] += 1
        if doc.index_state == 'failed':
            row['failed_count'] += 1
            row['failed_document_ids'].append(doc.public_id)
        if row['content_updated_at'] is None or doc.updated_at > row['content_updated_at']:
            row['content_updated_at'] = doc.updated_at
    return stats


def base_item(ctx, kb, stats):
    data = {'id': kb.public_id, 'name': kb.name, 'scope': kb.scope,
            'workspace': {'id': kb.workspace.public_id, 'name': kb.workspace.name} if kb.workspace_id else None,
            'cite_audience': kb.cite_audience, 'expert_cite_enabled': kb.expert_cite_enabled,
            'editor_department': ({'id': str(kb.editor_department_id), 'name': kb.editor_department.name}
                                  if kb.editor_department_id else None),
            'can_edit': can_edit(ctx, kb)}
    data.update(stats[kb.pk])
    data['content_updated_at'] = data['content_updated_at'] or kb.updated_at
    return data


def sorted_bases(qs):
    return sorted(qs, key=lambda kb: (SCOPE_ORDER.index(kb.scope), kb.name, kb.pk))


def index_summary(ctx, bases):
    docs = live_documents().filter(knowledge_base__in=bases)
    last = docs.filter(index_state='indexed').aggregate(at=Max('updated_at'))['at']
    return {'last_run_at': last, 'failed_count': docs.filter(index_state='failed').count()}


def base_detail(ctx, kb):
    data = base_item(ctx, kb, base_stats([kb]))
    data.update(
        owner=ws.person(kb.owner),
        editors=[{'id': str(u.pk), 'name': ws.full_name(u)} for u in kb.editors.order_by('pk')],
        citation_count=Citation.objects.filter(document__knowledge_base=kb, document__deleted_at__isnull=True,
                                               task__in=ws.visible_tasks(ctx)).count(),
        stale_policy={'threshold_days': STALE_THRESHOLD_DAYS}, version=kb.version, created_at=kb.created_at)
    return data


def update_base(request, ctx, kb, data):
    from backend.models import AuditEvent
    if not can_edit(ctx, kb):
        raise ValueError('PERMISSION_DENIED')
    if data['version'] != kb.version:
        raise ValueError('VERSION_CONFLICT')
    changes = []
    if 'cite_audience' in data:
        if data['cite_audience'] not in CITE_AUDIENCE_BY_SCOPE[kb.scope]:
            raise ValueError('VALIDATION_FAILED')
        kb.cite_audience = data['cite_audience']
        changes.append('引用范围')
    if 'expert_cite_enabled' in data:
        if not isinstance(data['expert_cite_enabled'], bool):
            raise ValueError('VALIDATION_FAILED')
        kb.expert_cite_enabled = data['expert_cite_enabled']
        changes.append('允许专家引用' if kb.expert_cite_enabled else '关闭专家引用')
    if 'editor_department_id' in data:
        value = data['editor_department_id']
        if value is None:
            kb.editor_department = None
        else:
            department = department_of(ctx, value)
            if department is None:
                raise ValueError('VALIDATION_FAILED')
            kb.editor_department = department
        changes.append('编辑部门')
    editors = None
    if 'editor_ids' in data:
        ids = data['editor_ids']
        if not isinstance(ids, list) or not all(isinstance(v, str) and v.isdigit() for v in ids) or len(ids) > 50:
            raise ValueError('VALIDATION_FAILED')
        members = Membership.objects.filter(account=ctx.account, user_id__in=[int(v) for v in ids]).select_related('user')
        editors = [m.user for m in members]
        if len(editors) != len(set(ids)):
            raise ValueError('VALIDATION_FAILED')
        changes.append('编辑者')
    kb.version += 1
    kb.save()
    if editors is not None:
        kb.editors.set(editors)
    AuditEvent.objects.create(actor=ctx.user, account=ctx.account, action='knowledge_base.permission.update',
                              request_id=request.request_id, target_type='knowledge_base', target_id=kb.public_id,
                              summary=('更新' + '、'.join(changes)) if changes else '更新权限')
    return kb


def audit_items(ctx, kb):
    from backend.models import AuditEvent
    return AuditEvent.objects.filter(account=ctx.account, target_type='knowledge_base',
                                     target_id=kb.public_id).select_related('actor').order_by('-created_at', '-pk')


def audit_dict(row):
    return {'action': row.action, 'actor': ({'id': str(row.actor_id), 'name': ws.full_name(row.actor)}
                                            if row.actor_id else None),
            'summary': row.summary, 'created_at': row.created_at}


# Documents ---------------------------------------------------------------------------

def visible_documents(ctx):
    return live_documents().filter(knowledge_base__in=visible_bases(ctx))


def get_document(ctx, public_id):
    doc = visible_documents(ctx).select_related('knowledge_base', 'knowledge_base__workspace', 'uploaded_by',
                                                 'source_artifact_version__author_expert').filter(
        public_id=public_id).first()
    if doc is None:
        raise ValueError('NOT_FOUND')
    return doc


def citation_counts(ctx, docs):
    rows = Citation.objects.filter(document__in=docs, task__in=ws.visible_tasks(ctx)).values(
        'document_id').annotate(n=Count('id'))
    return {row['document_id']: row['n'] for row in rows}


def is_stale(doc, today=None):
    today = today or clock.localdate()
    return bool(doc.data_date and (today - doc.data_date).days > STALE_THRESHOLD_DAYS)


def document_item(doc, counts):
    version = doc.source_artifact_version
    expert = version.author_expert if version else None
    return {'id': doc.public_id, 'title': doc.title, 'source_kind': doc.source_kind,
            'uploaded_by': {'id': str(doc.uploaded_by_id), 'name': ws.full_name(doc.uploaded_by)}
            if doc.uploaded_by_id else None,
            'source_expert': {'id': expert.public_id, 'name': expert.name, 'avatar_ref': ws.avatar_url(expert.avatar_ref)}
            if expert else None,
            'source_url': doc.source_url, 'refresh_interval': doc.refresh_interval, 'size_bytes': doc.size_bytes,
            'page_count': doc.page_count, 'index_state': doc.index_state, 'index_progress': doc.index_progress,
            'data_date': doc.data_date, 'is_stale': is_stale(doc), 'citation_count': counts.get(doc.pk, 0),
            'updated_at': doc.updated_at}


def document_detail(ctx, doc):
    data = document_item(doc, citation_counts(ctx, [doc]))
    kb = doc.knowledge_base
    entities = KnowledgeEntity.objects.filter(account=ctx.account, documents=doc).order_by('pk')
    data.update(
        knowledge_base={'id': kb.public_id, 'name': kb.name, 'scope': kb.scope,
                        'workspace': {'id': kb.workspace.public_id, 'name': kb.workspace.name}
                        if kb.workspace_id else None,
                        'expert_cite_enabled': kb.expert_cite_enabled},
        created_at=doc.created_at, can_edit=can_edit(ctx, kb),
        related_entities=[{'id': e.public_id, 'name': e.name, 'entity_type': e.entity_type} for e in entities])
    return data


def save_document_content(doc, body):
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')
    name = artifact_service.content_storage().save('knowledge/%s.json' % doc.public_id, ContentFile(data))
    return artifact_service.STORE_SCHEME + name


def read_document_content(doc):
    ref = doc.content_ref or ''
    if not ref.startswith(artifact_service.STORE_SCHEME):
        raise ValueError('SOURCE_UNAVAILABLE')
    try:
        with artifact_service.content_storage().open(ref[len(artifact_service.STORE_SCHEME):], 'rb') as handle:
            return json.loads(handle.read().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc


def open_original(body):
    name = body.get('original') if isinstance(body, dict) else None
    if not name:
        raise ValueError('SOURCE_UNAVAILABLE')
    storage = upload_storage() if body.get('original_store') == 'uploads' else artifact_service.content_storage()
    try:
        return storage.open(name, 'rb')
    except OSError as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc


def create_web_document(ctx, data):
    if data['source_kind'] != 'web':
        # Files go through POST documents/upload (multipart).
        raise ValueError('VALIDATION_FAILED')
    if not isinstance(data['knowledge_base_id'], str):
        raise ValueError('VALIDATION_FAILED')
    kb = get_base(ctx, data['knowledge_base_id'])
    if not can_edit(ctx, kb):
        raise ValueError('PERMISSION_DENIED')
    url = data['source_url']
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme not in ('http', 'https') or not parsed.netloc or len(url) > 200:
        raise ValueError('VALIDATION_FAILED')
    if data['refresh_interval'] not in REFRESH_INTERVALS:
        raise ValueError('VALIDATION_FAILED')
    title = ws.text_field(data.get('title'), 200, required=False) or (parsed.netloc + parsed.path).rstrip('/')[:200]
    return KnowledgeDocument.objects.create(
        account=ctx.account, knowledge_base=kb, title=title, source_kind='web', uploaded_by=ctx.user,
        source_url=url, refresh_interval=data['refresh_interval'], index_state='queued', content_ref='')


UPLOAD_EXTENSIONS = ('.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv', '.pptx', '.txt', '.md')
UPLOAD_MAX_BYTES = 20 * 1024 * 1024
_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f]')


def upload_max_bytes():
    return int(getattr(settings, 'KNOWLEDGE_UPLOAD_MAX_BYTES', UPLOAD_MAX_BYTES))


def upload_storage():
    location = getattr(settings, 'KNOWLEDGE_UPLOAD_DIR', None) or Path(settings.BASE_DIR) / 'tmp' / 'knowledge-uploads'
    return FileSystemStorage(location=location)


def _display_name(raw):
    # Browsers may send a full client path; only the last segment is kept, and only for display.
    name = _UNSAFE_NAME_CHARS.sub('', (raw or '').replace('\\', '/').rsplit('/', 1)[-1]).strip()
    return '' if name in ('.', '..') else name[:200]


def create_upload_document(ctx, kb_id, upload, title):
    kb = get_base(ctx, kb_id)
    if not can_edit(ctx, kb):
        raise ValueError('PERMISSION_DENIED')
    name = _display_name(upload.name)
    ext = Path(name).suffix.lower()
    allowed = tuple(getattr(settings, 'KNOWLEDGE_UPLOAD_EXTENSIONS', UPLOAD_EXTENSIONS))
    if not name or ext not in allowed or not 0 < upload.size <= upload_max_bytes():
        raise ValueError('VALIDATION_FAILED')
    title = ws.text_field(title, 200, required=False) or name
    doc = KnowledgeDocument.objects.create(
        account=ctx.account, knowledge_base=kb, title=title, source_kind='upload', uploaded_by=ctx.user,
        size_bytes=upload.size, index_state='queued', content_ref='')
    # The stored path never contains client-supplied text: <document id>/<random><whitelisted extension>.
    stored = upload_storage().save('%s/%s%s' % (doc.public_id, uuid.uuid4().hex, ext), upload)
    doc.content_ref = save_document_content(doc, {
        'original': stored, 'original_store': 'uploads', 'original_name': name,
        'content_type': mimetypes.guess_type(name)[0] or 'application/octet-stream', 'pages': []})
    doc.save(update_fields=['content_ref', 'updated_at'])
    return doc


def reindex(ctx, doc):
    if not can_edit(ctx, doc.knowledge_base):
        raise ValueError('PERMISSION_DENIED')
    updated = KnowledgeDocument.objects.filter(pk=doc.pk, index_state='failed').update(
        index_state='queued', index_progress=None, updated_at=clock.now())
    if not updated:
        raise ValueError('STATE_CONFLICT')
    doc.refresh_from_db()
    return doc


# Graph --------------------------------------------------------------------------------

def entities(ctx):
    return KnowledgeEntity.objects.filter(account=ctx.account)


def conflict_entity_ids(ctx):
    rows = (KnowledgeFact.objects.filter(account=ctx.account, conflict_key__isnull=False)
            .values('entity_id', 'conflict_key').annotate(n=Count('id')).filter(n__gt=1))
    return {row['entity_id'] for row in rows}


def entity_item(entity, conflicts):
    return {'id': entity.public_id, 'name': entity.name, 'entity_type': entity.entity_type,
            'subtitle': entity.subtitle, 'has_conflict': entity.pk in conflicts}


def fact_dict(fact):
    doc = fact.source_document
    return {'id': fact.public_id, 'statement': fact.statement, 'source_label': doc.title if doc else None,
            'source_document_id': doc.public_id if doc else None, 'source_date': fact.source_date,
            'validity': fact.validity, 'superseded_by': fact.superseded_by.public_id if fact.superseded_by else None,
            'conflict_key': fact.conflict_key}


def entity_detail(ctx, entity, include_superseded):
    all_facts = list(entity.facts.select_related('source_document', 'superseded_by').order_by('pk'))
    facts = all_facts if include_superseded else [f for f in all_facts if f.validity == 'current']
    groups = {}
    for fact in all_facts:
        if fact.conflict_key:
            groups.setdefault(fact.conflict_key, []).append(fact)
    conflicts = []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        current = [f for f in rows if f.validity == 'current'] or rows
        preferred = max(current, key=lambda f: (f.source_date or date.min, f.pk))
        conflicts.append({'conflict_key': key, 'facts': [fact_dict(f) for f in rows],
                          'preferred_fact_id': preferred.public_id})
    visible = ws.visible_tasks(ctx)
    cited = {}
    for task_id, at in Citation.objects.filter(fact__entity=entity, task__in=visible).values_list(
            'task_id', 'created_at'):
        cited[task_id] = max(at, cited.get(task_id, at))
    tasks = {t.pk: t for t in visible.filter(pk__in=cited)}
    cited_tasks = sorted(({'id': tasks[pk].public_id, 'title': tasks[pk].title, 'last_cited_at': at}
                          for pk, at in cited.items()), key=lambda row: row['last_cited_at'], reverse=True)
    data = entity_item(entity, {entity.pk} if conflicts else set())
    data.update(cited_task_count=len(cited_tasks), facts_total=len(all_facts), facts=[fact_dict(f) for f in facts],
                conflicts=conflicts, cited_tasks=cited_tasks,
                documents=[{'id': d.public_id, 'title': d.title}
                           for d in entity.documents.filter(pk__in=visible_documents(ctx)).order_by('pk')])
    return data


def resolve_conflict(ctx, entity, key, preferred_public_id):
    """Keep one fact of a conflict group current; the others become superseded by it (42 屏 "以较新为准")."""
    rows = list(KnowledgeFact.objects.select_for_update().select_related('source_document__knowledge_base')
                .filter(entity=entity, conflict_key=key).order_by('pk'))
    if len(rows) < 2:
        raise ValueError('NOT_FOUND')
    preferred = next((f for f in rows if f.public_id == preferred_public_id), None)
    if preferred is None:
        raise ValueError('VALIDATION_FAILED')
    if ctx.member.role != 'owner':
        bases = {f.source_document.knowledge_base for f in rows if f.source_document}
        if not bases or not all(can_edit(ctx, kb) for kb in bases):
            raise ValueError('PERMISSION_DENIED')
    for fact in rows:
        if fact.pk == preferred.pk:
            fact.validity, fact.superseded_by = 'current', None
        else:
            fact.validity, fact.superseded_by = 'superseded', preferred
        fact.conflict_key = None
        fact.save(update_fields=['validity', 'superseded_by', 'conflict_key', 'updated_at'])

def neighborhood(ctx, entity):
    relations = list(KnowledgeRelation.objects.filter(account=ctx.account).filter(
        Q(from_entity=entity) | Q(to_entity=entity)).select_related('from_entity', 'to_entity'))
    nodes = {entity.pk: entity}
    for rel in relations:
        nodes[rel.from_entity_id] = rel.from_entity
        nodes[rel.to_entity_id] = rel.to_entity
    edges = list(KnowledgeRelation.objects.filter(account=ctx.account, from_entity_id__in=nodes,
                                                  to_entity_id__in=nodes).select_related('from_entity', 'to_entity'))
    conflicts = conflict_entity_ids(ctx)
    return {'center_id': entity.public_id,
            'nodes': [dict(entity_item(node, conflicts), is_center=node.pk == entity.pk)
                      for node in sorted(nodes.values(), key=lambda n: (n.pk != entity.pk, n.pk))],
            'edges': [{'from': rel.from_entity.public_id, 'to': rel.to_entity.public_id, 'kind': rel.kind}
                      for rel in sorted(edges, key=lambda r: r.pk)]}

