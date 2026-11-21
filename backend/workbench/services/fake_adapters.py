"""Deterministic stand-ins for the replaceable integration points, so the app can be clicked through locally.

Only services.extensions.adapter() hands these out, and only when SAGE_WORKBENCH_FAKE_ADAPTERS=1 and
settings.ENVIRONMENT is development or test. They never call the network and never charge Credits.
"""
import hashlib

AVATARS = 53


def draft_generator(description, account, user):
    text = ' '.join(str(description or '').split())
    return {'name': '本地演示专家', 'title': (text or '本地演示专家')[:40],
            'responsibilities': text or '本地演示：根据描述生成的专家草稿。',
            'capability_tags': ['本地演示']}


def avatar_generator(expert, user):
    digest = int(hashlib.sha256(expert.public_id.encode()).hexdigest(), 16)
    return 'digital-employees/assets/light/512/AGT-%03d.webp' % (digest % AVATARS + 1)


def try_runner(expert, prompt, user, account):
    return {'reply': '（本地演示回复）%s 收到：%s' % (expert.name, prompt), 'credits_used': 0}


def connector_authorizer(connection, user):
    return {'credential_ref': 'fake:%s' % connection.public_id}


ADAPTERS = {
    'WORKBENCH_EXPERT_DRAFT_GENERATOR': draft_generator,
    'WORKBENCH_EXPERT_AVATAR_GENERATOR': avatar_generator,
    'WORKBENCH_EXPERT_TRY_RUNNER': try_runner,
    'WORKBENCH_CONNECTOR_AUTHORIZER': connector_authorizer,
}
