"""app/artifacts, app/reviews and app/citations (backend-plan 2.1-2.3; screens 13, 23, 41, 54-56)."""
import json
import secrets
from datetime import timedelta
from urllib.parse import quote

from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from .. import clock

from ..common import iso, json_body, page, query, respond
from ..ids import PUBLIC_ID_PATTERN
from ..models import (AccountPolicy, Artifact, ArtifactShare, ArtifactUserState, ArtifactVersion, Citation,
                      ContentReview, KnowledgeBase, KnowledgeDocument, TaskDelivery)
from ..services import artifacts as store
from ..services import workspaces as ws

KINDS = ('document', 'spreadsheet', 'presentation', 'webpage', 'image')
SHARE_AUDIENCES = ('only_me', 'workspace_members', 'encrypted_link')  # public_link is not offered (Q-P11 B)
SHARE_MAX_DAYS = 365
SEVERITY_ORDER = ('high', 'medium', 'low')
DIMENSIONS = ('platform_policy', 'market_law', 'brand', 'facts')
ID = PUBLIC_ID_PATTERN


# Serializers ----------------------------------------------------------------

def author(version):
    if version.author_expert_id:
        return {'kind': 'expert', 'name': version.author_expert.name, 'avatar_ref': ws.avatar_url(version.author_expert.avatar_ref)}
    if version.author_user_id:
        return {'kind': 'user', 'name': ws.full_name(version.author_user), 'avatar_ref': None}
    return None


def version_brief(version):
    if version is None:
        return None
    return {'number': version.number, 'state': version.state, 'label': version.label,
            'previous_number': version.number - 1 if version.number > 1 else None,
            'author': author(version), 'created_at': iso(version.created_at)}


def version_row(version):
    return {'number': version.number, 'state': version.state, 'label': version.label, 'author': author(version),
            'created_at': iso(version.created_at), 'change_summary': version.change_summary,
            'restored_from': version.restored_from.number if version.restored_from_id else None,
            'size_bytes': version.size_bytes}


def share_url(share):
    if share.token:
        return '/s/%s' % share.token
    return '/app/artifacts/%s' % share.artifact.public_id


def share_row(share, revoked=False):
    if share is None:
        return None
    row = {'id': share.public_id, 'audience': share.audience, 'url': share_url(share),
           'expires_at': iso(share.expires_at)}
    if revoked:
        row['revoked_at'] = iso(share.revoked_at)
    return row


def active_shares(artifact):
    return artifact.shares.filter(revoked_at__isnull=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=clock.now()))


def artifact_row(artifact, opened=None):
    version = artifact.current_version
    return {
        'id': artifact.public_id, 'title': artifact.title, 'kind': artifact.kind,
        'workspace': {'id': artifact.workspace.public_id, 'name': artifact.workspace.name}
        if artifact.workspace_id else None,
        'source_task': {'id': artifact.source_task.public_id, 'title': artifact.source_task.title}
        if artifact.source_task_id else None,
        'current_version': version_brief(version),
        'share': share_row(active_shares(artifact).order_by('-created_at').first()),
        'updated_at': iso(version.created_at if version else artifact.updated_at),
        'trashed_at': iso(artifact.trashed_at),
        'last_opened_at': iso(opened),
    }


def artifact_detail(ctx, artifact, level=None):
    level = level or store.artifact_access(ctx, artifact)
    state = ArtifactUserState.objects.filter(artifact=artifact, user=ctx.user).first()
    row = artifact_row(artifact, state.last_opened_at if state else None)
    version = artifact.current_version
    if row['current_version'] is not None:
        row['current_version']['citation_count'] = version.citations.count()
        row['current_version']['change_summary'] = version.change_summary
    delivery = TaskDelivery.objects.filter(artifact_version__artifact=artifact).order_by('-created_at').first()
    row['delivery'] = {'id': delivery.public_id, 'decision': delivery.decision,
                       'version': delivery.artifact_version.number} if delivery else None
    row['can_edit'] = level == 'edit' and artifact.trashed_at is None
    return row


# Lookups --------------------------------------------------------------------

def visible_artifacts(ctx):
    return (Artifact.objects.filter(account=ctx.account).filter(store.visible_artifacts_q(ctx))
            .select_related('workspace', 'source_task', 'current_version__author_expert',
                            'current_version__author_user').distinct())


def load(ctx, public_id):
    artifact = (Artifact.objects.select_related('workspace', 'source_task__owner', 'source_task__creator',
                                                'current_version')
                .filter(account=ctx.account, public_id=public_id).first())
    level = store.artifact_access(ctx, artifact)
    if level is None:
        raise ValueError('NOT_FOUND')
    return artifact, level


def load_for_edit(ctx, public_id):
    artifact, level = load(ctx, public_id)
    if level != 'edit':
        raise ValueError('PERMISSION_DENIED')
    return artifact


def get_version(artifact, number):
    version = (artifact.versions.select_related('author_expert', 'author_user', 'restored_from')
               .filter(number=int(number)).first())
    if version is None:
        raise ValueError('NOT_FOUND')
    return version


# Artifact reads ---------------------------------------------------------------

def list_artifacts(request, ctx):
    params = query(request, ('scope', 'kind', 'workspace', 'task', 'trashed', 'q', 'sort', 'offset'))
    base = visible_artifacts(ctx)
    qs = base
    if params.get('trashed', '') not in ('', '1', '0'):
        raise ValueError('VALIDATION_FAILED')
    qs = qs.filter(trashed_at__isnull=params.get('trashed') != '1')
    kind = params.get('kind')
    if kind is not None:
        if kind not in KINDS:
            raise ValueError('VALIDATION_FAILED')
        qs = qs.filter(kind=kind)
    workspace = params.get('workspace')
    if workspace == 'none':
        qs = qs.filter(workspace__isnull=True)
    elif workspace is not None:
        qs = qs.filter(workspace=ws.get_workspace(ctx, workspace)[0])
    task = None
    if params.get('task') is not None:
        task = ws.visible_tasks(ctx).filter(public_id=params['task']).first()
        if task is None:
            raise ValueError('NOT_FOUND')
        qs = qs.filter(source_task=task)
    scope = params.get('scope')
    mine = Q(created_by_user=ctx.user) | Q(source_task__creator__user=ctx.user)
    if scope == 'mine':
        qs = qs.filter(mine)
    elif scope == 'shared':
        qs = qs.exclude(mine)
    elif scope is not None:
        raise ValueError('VALIDATION_FAILED')
    if params.get('q'):
        qs = qs.filter(title__icontains=params['q'].strip())
    sort = params.get('sort', 'updated')
    opened = {}
    if sort == 'opened':
        states = ArtifactUserState.objects.filter(user=ctx.user, artifact__in=qs).order_by('-last_opened_at')
        opened = dict(states.values_list('artifact_id', 'last_opened_at'))
        by_pk = {item.pk: item for item in qs.filter(pk__in=list(opened))}
        rows = [by_pk[pk] for pk in opened if pk in by_pk]
    elif sort == 'updated':
        rows = qs.order_by('-current_version__created_at', '-pk')
    else:
        raise ValueError('VALIDATION_FAILED')
    if sort != 'opened':
        opened = dict(ArtifactUserState.objects.filter(user=ctx.user, artifact__in=qs)
                      .values_list('artifact_id', 'last_opened_at'))
    body = page(request, rows, lambda item: artifact_row(item, opened.get(item.pk)))
    live = base.filter(trashed_at__isnull=True)
    kinds = dict(live.order_by().values_list('kind').annotate(n=Count('pk', distinct=True)))
    per_workspace = (live.filter(workspace__isnull=False).order_by().values('workspace__public_id', 'workspace__name')
                     .annotate(n=Count('pk', distinct=True)).order_by('workspace__name'))
    workspaces = [{'id': row['workspace__public_id'], 'name': row['workspace__name'], 'count': row['n']}
                  for row in per_workspace]
    workspaces.append({'id': None, 'name': None, 'count': live.filter(workspace__isnull=True).count()})
    body['counts'] = {'all': live.count(), 'kinds': {name: kinds.get(name, 0) for name in KINDS},
                      'trash': base.filter(trashed_at__isnull=False).count(), 'workspaces': workspaces}
    if task is not None:
        task_rows = base.filter(source_task=task, trashed_at__isnull=True).annotate(n=Count('versions'))
        created = task_rows.filter(n=1).count()
        body['summary'] = {'created': created, 'modified': task_rows.count() - created}
    return body


def get_artifact(request, ctx, id):
    query(request)
    artifact, level = load(ctx, id)
    now = clock.now()
    # Two first opens at once (the page and its prefetch) can both miss the row; the loser just updates it.
    if not ArtifactUserState.objects.filter(artifact=artifact, user=ctx.user).update(last_opened_at=now):
        try:
            with transaction.atomic():
                ArtifactUserState.objects.create(artifact=artifact, user=ctx.user, account=ctx.account,
                                                 last_opened_at=now)
        except IntegrityError:
            ArtifactUserState.objects.filter(artifact=artifact, user=ctx.user).update(last_opened_at=now)
    return artifact_detail(ctx, artifact, level)


def list_versions(request, ctx, id):
    query(request, ('offset',))
    artifact, _ = load(ctx, id)
    versions = artifact.versions.select_related('author_expert', 'author_user', 'restored_from').order_by('-number')
    return page(request, versions, version_row)


def version_content(request, ctx, id, n):
    query(request)
    artifact, _ = load(ctx, id)
    version = get_version(artifact, n)
    return {'number': version.number, 'kind': artifact.kind, 'body': store.read_content(version)}


def version_citations(request, ctx, id, n):
    query(request, ('offset',))
    artifact, _ = load(ctx, id)
    version = get_version(artifact, n)
    rows = version.citations.select_related('document', 'fact').order_by('ref_index', 'created_at')

    def serialize(citation):
        document = citation.document
        label = citation.external_label or (document.title if document else None) or (
            citation.fact.statement if citation.fact_id else None)
        return {'id': citation.public_id, 'ref_index': citation.ref_index, 'locator': citation.locator,
                'document': {'id': document.public_id, 'title': document.title} if document else None,
                'cited_version': citation.cited_version, 'label': label}
    return page(request, rows, serialize)


def download_version(request, ctx, id, n):
    query(request)
    artifact, _ = load(ctx, id)
    version = get_version(artifact, n)
    body = store.read_content(version)
    if store.is_text(artifact, body):
        payload, content_type, extension = store.render_markdown(artifact.title, body), 'text/markdown', 'md'
    else:
        payload = json.dumps(body, ensure_ascii=False, indent=2)
        content_type, extension = 'application/json', 'json'
    response = HttpResponse(payload.encode('utf-8'), content_type=content_type + '; charset=utf-8')
    filename = '%s-v%d.%s' % (artifact.title, version.number, extension)
    response['Content-Disposition'] = "attachment; filename=\"artifact-v%d.%s\"; filename*=UTF-8''%s" % (
        version.number, extension, quote(filename))
    return response


def version_diff(request, ctx, id, n):
    params = query(request, ('base',))
    artifact, level = load(ctx, id)
    target = get_version(artifact, n)
    raw = params.get('base', str(target.number - 1))
    if not raw.isdigit() or int(raw) < 1 or int(raw) >= target.number:
        raise ValueError('VALIDATION_FAILED')
    base = get_version(artifact, raw)
    base_body, target_body = store.read_content(base), store.read_content(target)
    if store.is_text(artifact, base_body) and store.is_text(artifact, target_body):
        hunks = store.diff_hunks(base_body, target_body)
        changed = any('context' not in hunk for hunk in hunks)
    else:
        hunks, changed = [], base_body != target_body
    top = artifact.versions.order_by('-number').values_list('number', flat=True).first()
    return {
        'base': {'number': base.number, 'state': base.state, 'label': base.label, 'created_at': iso(base.created_at)},
        'target': {'number': target.number, 'state': target.state, 'label': target.label, 'author': author(target),
                   'created_at': iso(target.created_at), 'change_summary': target.change_summary},
        'hunks': hunks, 'changed': changed, 'restore_preview': {'next_number': top + 1},
        'can_restore': level == 'edit' and artifact.trashed_at is None,
    }


# Artifact writes ----------------------------------------------------------------

def patch_artifact(request, ctx, id):
    data = json_body(request, (), ('title', 'workspace_id'))
    artifact = load_for_edit(ctx, id)
    fields = []
    if 'title' in data:
        artifact.title = ws.text_field(data['title'], 120)
        fields.append('title')
    if 'workspace_id' in data:
        target = data['workspace_id']
        if target is None:
            artifact.workspace = None
        elif isinstance(target, str):
            workspace, member = ws.get_workspace(ctx, target)
            ws.require_editor(member)
            artifact.workspace = workspace
        else:
            raise ValueError('VALIDATION_FAILED')
        fields.append('workspace')
    if fields:
        artifact.save(update_fields=fields + ['updated_at'])
    return {'artifact': artifact_detail(ctx, artifact)}


def trash(request, ctx, id):
    artifact = load_for_edit(ctx, id)
    if artifact.trashed_at is not None:
        raise ValueError('STATE_CONFLICT')
    artifact.trashed_at = clock.now()
    artifact.save(update_fields=['trashed_at', 'updated_at'])
    return {'artifact': {'id': artifact.public_id, 'trashed_at': iso(artifact.trashed_at)}}


def untrash(request, ctx, id):
    artifact = load_for_edit(ctx, id)
    if artifact.trashed_at is None:
        raise ValueError('STATE_CONFLICT')
    artifact.trashed_at = None
    artifact.save(update_fields=['trashed_at', 'updated_at'])
    return {'artifact': {'id': artifact.public_id, 'trashed_at': None}}


def create_share(request, ctx, id):
    data = json_body(request, ('audience',), ('expires_in_days', 'secret'))
    artifact = load_for_edit(ctx, id)
    if artifact.trashed_at is not None:
        raise ValueError('STATE_CONFLICT')
    audience = data['audience']
    if audience not in SHARE_AUDIENCES:
        raise ValueError('VALIDATION_FAILED')
    days = data.get('expires_in_days')
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= SHARE_MAX_DAYS):
        raise ValueError('VALIDATION_FAILED')
    secret = data.get('secret')
    if audience == 'encrypted_link':
        if not isinstance(secret, str) or not 6 <= len(secret) <= 64:
            raise ValueError('VALIDATION_FAILED')
    elif secret is not None:
        raise ValueError('VALIDATION_FAILED')
    if audience == 'workspace_members' and artifact.workspace_id is None:
        raise ValueError('VALIDATION_FAILED')
    if audience == 'encrypted_link':
        policy = AccountPolicy.objects.filter(account=ctx.account).first()
        if policy and 'external_publish' in (policy.approval_required_actions or []):
            raise ValueError('POLICY_APPROVAL_REQUIRED')
    limited = ws.rate_limited(request, ctx, 'artifact-share')
    if limited:
        return limited
    now = clock.now()
    with transaction.atomic():
        artifact.shares.filter(revoked_at__isnull=True).update(revoked_at=now)
        share = ArtifactShare.objects.create(
            account=ctx.account, artifact=artifact, audience=audience, created_by=ctx.user,
            token=secrets.token_urlsafe(32) if audience == 'encrypted_link' else None,
            secret_hash=make_password(secret) if secret else None,
            expires_at=now + timedelta(days=days) if days else None)
    return respond({'share': share_row(share)}, 201)


def _revoke(share):
    if share is None:
        raise ValueError('STATE_CONFLICT')
    share.revoked_at = clock.now()
    share.save(update_fields=['revoked_at', 'updated_at'])
    return {'share': share_row(share, revoked=True)}


def revoke_share(request, ctx, id, sid):
    artifact = load_for_edit(ctx, id)
    share = artifact.shares.filter(public_id=sid).first()
    if share is None:
        raise ValueError('NOT_FOUND')
    return _revoke(share if share.revoked_at is None else None)


def revoke_current_share(request, ctx, id):
    artifact = load_for_edit(ctx, id)
    return _revoke(active_shares(artifact).order_by('-created_at').first())


def restore_version(request, ctx, id, n):
    artifact = load_for_edit(ctx, id)
    data = json_body(request, ('client_request_id',))
    key = ws.text_field(data['client_request_id'], 64)
    with transaction.atomic():
        Artifact.objects.select_for_update().filter(pk=artifact.pk).first()
        previous = (ArtifactVersion.objects.select_related('restored_from')
                    .filter(account=ctx.account, client_request_id=key).first())
        if previous is not None:
            if previous.artifact_id != artifact.pk or previous.restored_from is None \
                    or previous.restored_from.number != int(n):
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return respond({'version': {'number': previous.number, 'state': previous.state,
                                        'restored_from': previous.restored_from.number}}, 201)
        if artifact.trashed_at is not None:
            raise ValueError('STATE_CONFLICT')
        source = get_version(artifact, n)
        if source.pk == artifact.current_version_id:
            raise ValueError('STATE_CONFLICT')
        version = store.create_version(artifact, None, state='draft', author_user=ctx.user,
                                       change_summary='恢复自 v%d' % source.number, restored_from=source,
                                       content_ref=source.content_ref, size_bytes=source.size_bytes,
                                       client_request_id=key)
    return respond({'version': {'number': version.number, 'state': version.state,
                                'restored_from': source.number}}, 201)


# Reviews ------------------------------------------------------------------------

def load_review(ctx, public_id):
    review = (ContentReview.objects.select_related('task__owner', 'task__creator', 'task__workspace',
                                                   'verifier_expert', 'result_version__artifact')
              .filter(account=ctx.account, public_id=public_id).first())
    level = ws.task_access(ctx, review.task) if review else None
    if level is None:
        raise ValueError('NOT_FOUND')
    return review, level


def review_targets(review):
    through = ContentReview.artifact_versions.through
    return [row.artifactversion for row in through.objects.filter(contentreview=review).order_by('pk')
            .select_related('artifactversion__artifact__current_version')]


def version_ref(version):
    return ({'artifact_id': version.artifact.public_id, 'number': version.number, 'title': version.artifact.title,
             'label': version.label} if version else None)


def issue_row(issue):
    return {
        'id': issue.public_id, 'dimension': issue.dimension, 'severity': issue.severity, 'title': issue.title,
        'location': issue.location, 'original_text': issue.original_text, 'suggested_text': issue.suggested_text,
        'highlight_spans': issue.highlight_spans, 'basis': issue.basis,
        'basis_rule': {'version': issue.basis_rule_version.number, 'index': issue.basis_rule_index}
        if issue.basis_rule_version_id else None,
        'action': issue.action,
        'suggested_artifact_version': version_ref(issue.suggested_artifact_version),
    }


def get_review(request, ctx, id):
    query(request)
    review, level = load_review(ctx, id)
    reviewers = [row.expert for row in ContentReview.reviewers.through.objects.filter(contentreview=review)
                 .order_by('pk').select_related('expert')]
    issues = sorted(review.issues.select_related('basis_rule_version', 'suggested_artifact_version__artifact'),
                    key=lambda item: (SEVERITY_ORDER.index(item.severity), DIMENSIONS.index(item.dimension),
                                      item.created_at, item.pk))
    by_dimension = {name: (sum(1 for item in issues if item.dimension == name) if name in review.dimensions else None)
                    for name in DIMENSIONS}
    return {
        'id': review.public_id, 'state': review.state, 'conclusion': review.conclusion,
        'dimensions': review.dimensions, 'task': {'id': review.task.public_id, 'title': review.task.title},
        'reviewers': [ws.expert_ref(expert) for expert in reviewers],
        'verifier_expert': ws.expert_ref(review.verifier_expert),
        'targets': [{'artifact_id': version.artifact.public_id, 'title': version.artifact.title,
                     'version': version.number, 'kind': version.artifact.kind} for version in review_targets(review)],
        'target_platform': review.target_platform, 'target_market': review.target_market,
        'counts': {'issues': len(issues), 'by_dimension': by_dimension},
        'issues': [issue_row(issue) for issue in issues],
        'result_version': version_ref(review.result_version),
        'can_edit': level == 'edit',
    }


def _apply_issue(review, issue, user):
    """Apply one issue; returns the affected ArtifactVersion or None if the text was not found."""
    if issue.suggested_artifact_version_id:
        candidate = issue.suggested_artifact_version
        artifact = candidate.artifact
        artifact.current_version = candidate
        artifact.save(update_fields=['current_version', 'updated_at'])
        return candidate
    draft = review.result_version
    for reviewed in review_targets(review):
        artifact = reviewed.artifact
        current = artifact.current_version
        if current is None or current.pk not in (reviewed.pk, draft.pk if draft else None):
            continue
        body = store.read_content(current)
        if not store.is_text(artifact, body):
            continue
        new_body, replaced = store.replace_text(body, issue.original_text, issue.suggested_text)
        if not replaced:
            continue
        if draft is not None and current.pk == draft.pk:
            # Single applies accumulate into one draft version (Q-F-7 A).
            draft.content_ref, draft.size_bytes = store.save_content(artifact, draft.number, new_body)
            draft.save(update_fields=['content_ref', 'size_bytes', 'updated_at'])
            return draft
        draft = store.create_version(artifact, new_body, state='draft', author_user=user,
                                     change_summary='按内容审核建议改写')
        review.result_version = draft
        review.save(update_fields=['result_version', 'updated_at'])
        return draft
    return None


def _targets_current(review):
    draft_pk = review.result_version_id
    return all(version.artifact.current_version_id in (version.pk, draft_pk) for version in review_targets(review))


def patch_issue(request, ctx, id, iid):
    review, level = load_review(ctx, id)
    if level != 'edit':
        raise ValueError('PERMISSION_DENIED')
    data = json_body(request, ('action',))
    issue = review.issues.select_related('suggested_artifact_version__artifact').filter(public_id=iid).first()
    if issue is None:
        raise ValueError('NOT_FOUND')
    if data['action'] not in ('applied', 'ignored'):
        raise ValueError('VALIDATION_FAILED')
    with transaction.atomic():
        review = ContentReview.objects.select_for_update().select_related('result_version__artifact').get(pk=review.pk)
        issue.refresh_from_db(fields=['action'])
        if review.state != 'reviewed' or issue.action != 'pending':
            raise ValueError('STATE_CONFLICT')
        affected = None
        if data['action'] == 'applied':
            if not _targets_current(review):
                raise ValueError('STATE_CONFLICT')
            affected = _apply_issue(review, issue, ctx.user)
            if affected is None:
                raise ValueError('STATE_CONFLICT')
        issue.action = data['action']
        issue.save(update_fields=['action', 'updated_at'])
    return {'issue': {'id': issue.public_id, 'action': issue.action},
            'result_version': version_ref(affected or review.result_version),
            'artifact_version': {'number': affected.number} if affected else None}


def _finish_rewrite(review_pk, user):
    with transaction.atomic():
        review = ContentReview.objects.select_for_update().select_related('result_version__artifact').get(pk=review_pk)
        if review.state != 'rewriting':
            return
        for issue in review.issues.filter(action='pending').select_related('suggested_artifact_version__artifact'):
            try:
                affected = _apply_issue(review, issue, user)
            except ValueError:
                affected = None
            if affected is not None:
                issue.action = 'applied'
                issue.save(update_fields=['action', 'updated_at'])
        review.state = 'rewritten'
        review.save(update_fields=['state', 'updated_at'])


def apply_all(request, ctx, id):
    review, level = load_review(ctx, id)
    if level != 'edit':
        raise ValueError('PERMISSION_DENIED')
    data = json_body(request, (), ('client_request_id',))
    key = ws.text_field(data['client_request_id'], 64) if 'client_request_id' in data else None
    with transaction.atomic():
        review = ContentReview.objects.select_for_update().get(pk=review.pk)
        if key is not None:
            previous = ContentReview.objects.filter(account=ctx.account, client_request_id=key).first()
            if previous is not None:
                if previous.pk != review.pk:
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                return respond({'review': {'id': review.public_id, 'state': review.state}}, 202)
        if (review.state != 'reviewed' or not review.issues.filter(action='pending').exists()
                or not _targets_current(review)):
            raise ValueError('STATE_CONFLICT')
        review.state = 'rewriting'
        review.client_request_id = key
        review.save(update_fields=['state', 'client_request_id', 'updated_at'])
        user = ctx.user
        transaction.on_commit(lambda: _finish_rewrite(review.pk, user))
    return respond({'review': {'id': review.public_id, 'state': review.state}}, 202)


def export_review(request, ctx, id):
    # 导出报告：backend-plan 未定格式，定 JSON 报告（detail 字段全集 + 导出时间）。
    query(request)
    review, level = load_review(ctx, id)
    detail = get_review(request, ctx, id)
    detail['created_at'] = iso(review.created_at)
    detail['updated_at'] = iso(review.updated_at)
    detail['generated_at'] = iso(clock.now())
    return {'report': detail}


# Citations ------------------------------------------------------------------------

def base_visible(ctx, kb):
    if kb.scope == 'account':
        return True
    if kb.scope == 'personal':
        return kb.owner_id == ctx.user.pk
    member = ws.my_member(ctx, kb.workspace) if kb.workspace_id else None
    return kb.owner_id == ctx.user.pk or (member is not None and member.role != 'guest')


def document_visible(ctx, document):
    return base_visible(ctx, document.knowledge_base)


def list_citations(request, ctx):
    # document 与 knowledge_base 二选一（54 屏技能输出：按知识库聚合引用）。
    params = query(request, ('document', 'knowledge_base', 'offset'))
    if bool(params.get('document')) == bool(params.get('knowledge_base')):
        raise ValueError('INVALID_REQUEST')
    if params.get('document'):
        document = (KnowledgeDocument.objects.select_related('knowledge_base__workspace')
                    .filter(account=ctx.account, public_id=params['document'], deleted_at__isnull=True).first())
        if document is None or not document_visible(ctx, document):
            raise ValueError('NOT_FOUND')
        rows = Citation.objects.filter(document=document, task__in=ws.visible_tasks(ctx).values('pk'))
    else:
        kb = (KnowledgeBase.objects.select_related('workspace')
              .filter(account=ctx.account, public_id=params['knowledge_base']).first())
        if kb is None or not base_visible(ctx, kb):
            raise ValueError('NOT_FOUND')
        rows = Citation.objects.filter(document__knowledge_base=kb,
                                       task__in=ws.visible_tasks(ctx).values('pk'))
    # Only citations from tasks the reader can see are listed (Q-E-23 B).
    rows = (rows.select_related('task__lead_expert', 'artifact_version__artifact')
            .order_by('-created_at', '-pk'))

    def serialize(citation):
        artifact = citation.artifact_version.artifact if citation.artifact_version_id else None
        return {'id': citation.public_id,
                'task': {'id': citation.task.public_id, 'title': citation.task.title,
                         'lead_expert': ws.expert_ref(citation.task.lead_expert)},
                'artifact': {'id': artifact.public_id, 'title': artifact.title} if artifact else None,
                'locator': citation.locator, 'ref_index': citation.ref_index, 'created_at': iso(citation.created_at)}
    return page(request, rows, serialize)


VERSION = r'^artifacts/(?P<id>%s)/versions/(?P<n>[0-9]{1,5})/%s$'

ROUTES = [
    ('GET', 'artifacts', list_artifacts),
    ('GET', 'artifacts/{id}', get_artifact),
    ('PATCH', 'artifacts/{id}', patch_artifact),
    ('GET', 'artifacts/{id}/versions', list_versions),
    ('GET', VERSION % (ID, 'content'), version_content),
    ('GET', VERSION % (ID, 'citations'), version_citations),
    ('GET', VERSION % (ID, 'download'), download_version),
    ('GET', VERSION % (ID, 'diff'), version_diff),
    ('POST', VERSION % (ID, 'restore'), restore_version),
    ('POST', 'artifacts/{id}/trash', trash),
    ('DELETE', 'artifacts/{id}/trash', untrash),
    ('POST', 'artifacts/{id}/shares', create_share),
    ('DELETE', 'artifacts/{id}/shares', revoke_current_share),
    ('DELETE', 'artifacts/{id}/shares/{sid}', revoke_share),
    ('GET', 'reviews/{id}/export', export_review),
    ('GET', 'reviews/{id}', get_review),
    ('PATCH', 'reviews/{id}/issues/{iid}', patch_issue),
    ('POST', 'reviews/{id}/apply-all', apply_all),
    ('GET', 'citations', list_citations),
]
