from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PREVIOUS = [('workbench', None), ('commerce', '0035_protect_enterprise_change_history'),
            ('backend', '0015_member_removal_history')]


class MigrationRoundTripTests(TransactionTestCase):
    """backend 0016 / commerce 0036 / workbench 0001 apply, roll back and re-apply cleanly."""

    def _columns(self, table):
        with connection.cursor() as cursor:
            return {column.name for column in connection.introspection.get_table_description(cursor, table)}

    def _tables(self):
        return set(connection.introspection.table_names())

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    def test_workbench_0003_round_trip(self):
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        targets = [node for node in leaves if node[0] in ('backend', 'commerce', 'workbench')]
        self.addCleanup(self._migrate, targets)
        added = {'workbench_inboxsource': 'version', 'workbench_automationrun': 'client_request_id',
                 'workbench_artifactversion': 'client_request_id', 'workbench_workspace': 'version',
                 'workbench_contentreview': 'client_request_id', 'workbench_authorizationrequest': 'version'}
        for table, column in added.items():
            self.assertIn(column, self._columns(table), table)
        self._migrate([('workbench', '0002_artifactversion_label')])
        for table, column in added.items():
            self.assertNotIn(column, self._columns(table), table)
        self._migrate(targets)
        for table, column in added.items():
            self.assertIn(column, self._columns(table), table)

    def test_round_trip(self):
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        targets = [node for node in leaves if node[0] in ('backend', 'commerce', 'workbench')]
        self.addCleanup(self._migrate, targets)
        self.assertIn('workbench_task', self._tables())
        self.assertIn('is_security_admin', self._columns('backend_membership'))

        self._migrate(PREVIOUS)
        tables = self._tables()
        self.assertFalse({name for name in tables if name.startswith('workbench_')})
        self.assertNotIn('commerce_department', tables)
        self.assertNotIn('commerce_departmentmember', tables)
        self.assertNotIn('display_number', self._columns('commerce_order'))
        self.assertNotIn('step_key', self._columns('commerce_taskcostevent'))
        self.assertNotIn('is_security_admin', self._columns('backend_membership'))
        self.assertFalse({'assistant_name', 'theme_style', 'preferred_name'} & self._columns('backend_accountprofile'))
        self.assertFalse({'device_name', 'last_seen_at'} & self._columns('backend_accountsession'))
        self.assertFalse({'target_type', 'target_id', 'summary'} & self._columns('backend_auditevent'))
        self.assertFalse({'successor_membership_id', 'memory_purge_acknowledged_at'} &
                         self._columns('backend_memberremoval'))

        self._migrate(targets)
        tables = self._tables()
        self.assertIn('workbench_task', tables)
        self.assertIn('commerce_department', tables)
        self.assertIn('display_number', self._columns('commerce_order'))
        self.assertIn('successor_membership_id', self._columns('backend_memberremoval'))
