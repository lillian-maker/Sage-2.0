"""Factories and fake adapters shared by the experts-domain tests."""
from backend.workbench.models import (ConnectorConnection, Expert, ExpertCapability, Extension, ExtensionInstall,
                                      KnowledgeBase, KnowledgeDocument, Task, TaskStep, Workspace, WorkspaceMember)


def system_expert(code, name, title, group='role', domain='产品与创新', specialty=None, **extra):
    return Expert.objects.create(account=None, source='system', group=group, catalog_code=code, name=name,
                                 title=title, domain=domain if group == 'role' else '', specialty=specialty,
                                 responsibilities=extra.pop('responsibilities', title + '。'),
                                 capability_tags=extra.pop('capability_tags', []), version_label='v1.0',
                                 status='published', share_scope='account', communication_style='conclusion_first',
                                 uncertainty_policy='ask_first', avatar_ref='assets/people/%s.webp' % code, **extra)


def custom_expert(account, user, name, status='published', share_scope='account', **extra):
    return Expert.objects.create(account=account, source='custom', group='role', name=name,
                                 title=extra.pop('title', name + '岗位'),
                                 responsibilities=extra.pop('responsibilities', '负责' + name), version_label='v1.0',
                                 status=status, share_scope=share_scope, created_by=user,
                                 communication_style='concise', uncertainty_policy='ask_first',
                                 wizard_step=1 if status == 'draft' else None, **extra)


def extension(name, kind='skill', tier='official', account=None, version='v1.0', **extra):
    return Extension.objects.create(account=account, kind=kind, name=name, description=extra.pop('description', name),
                                    publisher_tier=tier, latest_version=version, **extra)


def install(account, ext, user, enabled=True):
    return ExtensionInstall.objects.create(account=account, extension=ext, installed_version=ext.latest_version,
                                           enabled=enabled, installed_by=user)


def connection(account, ext, state='not_connected', owner=None):
    return ConnectorConnection.objects.create(account=account, extension=ext, state=state, owner_user=owner)


def capability(account, expert, ext, required_for=None):
    return ExpertCapability.objects.create(account=account, expert=expert, extension=ext, required_for=required_for)


def workspace(account, owner, name='德国市场进入', members=()):
    row = Workspace.objects.create(account=account, name=name, created_from='blank', created_by=owner)
    WorkspaceMember.objects.create(account=account, workspace=row, user=owner, role='owner', state='active')
    for user, role in members:
        WorkspaceMember.objects.create(account=account, workspace=row, user=user, role=role, state='active')
    return row


def task(account, membership, title, lead_expert=None, **extra):
    return Task.objects.create(account=account, title=title, creator=membership, owner=membership,
                               lead_expert=lead_expert, model_mode='auto', **extra)


def step(task_row, order, expert, stage='research', skill=None):
    return TaskStep.objects.create(account=task_row.account, task=task_row, order=order, stage=stage,
                                   title='步骤 %d' % order, expert=expert, skill=skill)


def knowledge_base(account, owner, name, scope='account', workspace_row=None, cite='account_all', **extra):
    return KnowledgeBase.objects.create(account=account, name=name, scope=scope, workspace=workspace_row,
                                        owner=owner, cite_audience=cite, **extra)


def document(kb, title, user=None, **extra):
    extra.setdefault('index_state', 'indexed')
    return KnowledgeDocument.objects.create(account=kb.account, knowledge_base=kb, title=title, source_kind='upload',
                                            uploaded_by=user, content_ref=extra.pop('content_ref', ''), **extra)


# Fake adapters (wired through override_settings) ------------------------------------

def fake_generator(description, account, user):
    return {'name': '德语内容官', 'title': '德国市场内容本地化与合规审校',
            'responsibilities': '把中文产品与营销内容改写为地道的德语版本。',
            'capability_tags': ['德语本地化', '宣称合规', '人工确认点']}


def failing_generator(description, account, user):
    raise ConnectionError('down')


def fake_avatar(expert, user):
    return 'generated/%s-2.webp' % expert.public_id


def fake_try(expert, prompt, user, account):
    return {'reply': 'Lange Akkulaufzeit', 'credits_used': 2}


def fake_authorizer(connection, user):
    return {'credential_ref': 'vault:test-%s' % connection.public_id}


def redirect_authorizer(connection, user):
    return {'authorize_url': 'https://auth.example.com/start'}


def failing_authorizer(connection, user):
    raise ConnectionError('down')
