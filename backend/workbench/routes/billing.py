"""Billing-domain app/ routes: authorizations (C-A-5, C-A-6), usage (C-C-8, C-C-9) and org settings
(departments, member department, policy and extension allowlist, audit, offboarding preview).

app/grants is served by routes/account.py.
"""
from .. import common
from ..models import HandoffItem
from ..services import billing, billing_documents, org_people

MEMBER = r'(?P<mid>[1-9][0-9]{0,9})'


def detail(request, ctx, id):
    common.query(request)
    return billing.authorization_dict(ctx, billing.find_authorization(ctx, id))


def approve(request, ctx, id):
    data = common.json_body(request, ('client_request_id',), optional=('decision_scope', 'version'))
    return billing.approve(ctx, id, data)


def reject(request, ctx, id):
    data = common.json_body(request, ('client_request_id',), optional=('version', 'reason'))
    return billing.reject(ctx, id, data)


def revise(request, ctx, id):
    data = common.json_body(request, ('scope_params', 'client_request_id'), optional=('version',))
    return billing.revise(ctx, id, data)


def estimate(request, ctx, id):
    data = common.json_body(request, ('client_request_id',))
    return common.respond(billing.estimate(ctx, id, data), status=202)


def usage_summary(request, ctx):
    common.query(request)
    return billing.usage_summary(ctx)


def usage_tasks(request, ctx):
    params = common.query(request, ('offset', 'sort'))
    sort = params.get('sort', 'recent')
    if sort not in ('recent', 'credits'):
        raise ValueError('INVALID_REQUEST')
    head, items = billing.usage_tasks(ctx, sort)
    return {**head, **common.page(request, items, lambda row: row)}


def departments(request, ctx):
    common.query(request)
    return billing.departments(ctx)


def create_department(request, ctx):
    return common.respond(billing.create_department(ctx, common.json_body(request, ('name',))), status=201)


def member_department(request, ctx, mid):
    data = common.json_body(request, ('department_id', 'is_lead', 'request_key'))
    result = billing.assign_department(ctx, mid, data)
    org_people.record_member(billing.target_member(ctx.account, mid))
    return result


def policy(request, ctx):
    common.query(request)
    return billing.policy_dict(ctx, billing.policy(ctx.account))


def update_policy(request, ctx):
    return billing.update_policy(ctx, common.json_body(request, ('version',), optional=tuple(billing.POLICY_LABELS)))


def add_extension(request, ctx):
    return common.respond(billing.add_allow_entry(ctx, common.json_body(request, ('extension_id',))), status=201)


def remove_extension(request, ctx, id):
    return billing.remove_allow_entry(ctx, id)


def audit(request, ctx):
    params = common.query(request, ('offset', 'action', 'actor'))
    members = billing.audit_members(ctx.account)
    return common.page(request, billing.audit_events(ctx, params), lambda event: billing.audit_dict(event, members))


def audit_export(request, ctx):
    common.query(request)
    return billing.audit_export(ctx)


def offboarding_preview(request, ctx, mid):
    common.query(request, ('offset',))
    target, tasks, counts = billing.offboarding_preview(ctx, mid)
    # 已办理离职后任务被暂停，交接清单展示的是接手人确认后会恢复到的状态
    resume = dict(HandoffItem.objects.filter(
        account=ctx.account, removal__membership=target, kind='task', resume_status__isnull=False,
    ).values_list('target_id', 'resume_status'))

    def serialize(task):
        status = task.status
        if task.status == 'paused' and task.pause_reason == 'handoff' and resume.get(task.public_id):
            status = resume[task.public_id]
        return {'id': task.public_id, 'title': task.title, 'status': status,
                'used_credits': billing.task_used_credits(ctx.account, task),
                'last_activity_at': common.iso(task.last_activity_at)}
    return {'member': {'id': target.pk, 'display_name': billing.display_name(target.user)},
            'tasks': common.page(request, tasks, serialize), **counts}


def documents_display(request, ctx):
    common.query(request)
    return billing_documents.display(ctx)


def document_pdf(request, ctx, oid):
    params = common.query(request, ('document',))
    return billing_documents.document_pdf(ctx, oid, params.get('document', 'bill'))


ROUTES = [
    ('GET', 'authorizations/{id}', detail),
    ('POST', 'authorizations/{id}/approve', approve),
    ('POST', 'authorizations/{id}/reject', reject),
    ('POST', 'authorizations/{id}/revise', revise),
    ('POST', 'authorizations/{id}/estimate', estimate),
    ('GET', 'usage/summary', usage_summary),
    ('GET', 'usage/tasks', usage_tasks),
    ('GET', 'org/departments', departments),
    ('POST', 'org/departments', create_department),
    ('POST', '^org/members/%s/department' % MEMBER, member_department),
    ('GET', '^org/members/%s/offboarding-preview' % MEMBER, offboarding_preview),
    ('GET', 'org/policy', policy),
    ('PATCH', 'org/policy', update_policy),
    ('POST', 'org/policy/extensions', add_extension),
    ('DELETE', 'org/policy/extensions/{id}', remove_extension),
    ('GET', 'org/audit', audit),
    ('GET', 'org/audit/export', audit_export),
    ('GET', 'billing/display', documents_display),
    ('GET', '^billing/invoices/(?P<oid>[^/]+)/pdf', document_pdf),
]
