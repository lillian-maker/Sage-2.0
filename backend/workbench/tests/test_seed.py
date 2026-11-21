from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from backend.models import BillingAccount, Membership
from backend.workbench.models import Task, Workspace, WorkspaceMember
from backend.workbench.seeds.workspace_tasks import WORKSPACE_TASKS


class SeedAppTests(TestCase):
    def test_refuses_production(self):
        with override_settings(ENVIRONMENT='production'):
            with self.assertRaisesRegex(CommandError, 'SEED_ENVIRONMENT_FORBIDDEN'):
                call_command('seed_app', stdout=StringIO())
        self.assertFalse(BillingAccount.objects.filter(name='Sage Demo 企业').exists())

    def test_core_seed_is_idempotent(self):
        for _ in range(2):
            out = StringIO()
            call_command('seed_app', stdout=out)
            self.assertIn('core', out.getvalue())
        account = BillingAccount.objects.get(name='Sage Demo 企业', kind='business')
        roles = dict(Membership.objects.filter(account=account, is_active=True).values_list('user__email', 'role'))
        self.assertEqual(roles, {'lillian@sagedemo.com': 'owner', 'chen@sage.demo': 'member',
                                 'mark@sage.demo': 'member', 'wangmin@sage.demo': 'member',
                                 'zhaohui@sage.demo': 'member', 'suntao@sage.demo': 'member'})
        # 53/75 设计是办理离职前状态：Wang Min 的 membership 保持 active，离职交接由 MemberRemoval 记录承载。
        self.assertEqual(list(Membership.objects.filter(account=account, is_active=False).values_list(
            'user__email', flat=True)), [])
        workspace = Workspace.objects.get(account=account, name='德国市场进入')
        members = {(row.user.email if row.user else row.invite_email, row.role, row.state)
                   for row in WorkspaceMember.objects.filter(workspace=workspace)}
        # Wang Min 曾接任所有者，offboard 把所有权转回 Lillian 后她的行保留为 removed（53 交接记录）。
        self.assertEqual(members, {('lillian@sagedemo.com', 'owner', 'active'),
                                   ('chen@sage.demo', 'editor', 'active'),
                                   ('mark@sage.demo', 'viewer', 'active'),
                                   ('wangmin@sage.demo', 'owner', 'removed'),
                                   ('anna@partner.de', 'guest', 'invited')})
        self.assertFalse(Membership.objects.filter(user__email='anna@partner.de', account=account).exists())
        # Screens 631 / 17 / 18: "任务 6" in the core workspace after every seed ran.
        tasks = Task.objects.filter(account=account, workspace=workspace, archived_at__isnull=True)
        self.assertEqual(sorted(tasks.values_list('title', flat=True)), sorted(WORKSPACE_TASKS))

    def test_legacy_demo_email_is_renamed_in_place(self):
        from backend.models import LoginIdentity
        from backend.workbench.seeds import core
        core._user('lillian@example.com', 'Lillian', 'Li')
        call_command('seed_app', '--only', 'core', stdout=StringIO())
        identity = LoginIdentity.objects.get(method='email', identifier='lillian@sagedemo.com')
        self.assertEqual((identity.user.email, identity.user.username), ('lillian@sagedemo.com', 'lillian@sagedemo.com'))
        self.assertFalse(LoginIdentity.objects.filter(identifier='lillian@example.com').exists())
        self.assertEqual(Membership.objects.filter(user=identity.user, account__kind='business').count(), 1)

    def test_only_selected_modules(self):
        out = StringIO()
        call_command('seed_app', '--only', 'core', stdout=out)
        self.assertIn('core', out.getvalue())
        with self.assertRaisesRegex(CommandError, 'UNKNOWN_SEED'):
            call_command('seed_app', '--only', 'nope', stdout=StringIO())
