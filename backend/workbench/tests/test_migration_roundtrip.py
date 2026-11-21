"""Round-trip the migrations this project added: every workbench migration after 0001, commerce 0036, backend 0016.

Workbench migrations are discovered from disk, so a new migration is covered without editing this file:
each one is rolled back to its predecessor and migrated forward again.
"""
from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase


def workbench_pairs():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    names = sorted(name for app, name in loader.disk_migrations if app == 'workbench')
    return list(zip(names, names[1:]))


class MigrationRoundTripTests(TransactionTestCase):
    def round_trip(self, app, previous):
        call_command('migrate', app, previous, verbosity=0, interactive=False)
        call_command('migrate', app, verbosity=0, interactive=False)

    def test_workbench_each_migration(self):
        pairs = workbench_pairs()
        names = [current for _, current in pairs]
        # 0002–0008 existed when this test was written; a missing one means discovery broke, not that it passed.
        for expected in ('0002', '0003', '0004', '0005', '0006', '0007', '0008'):
            self.assertTrue(any(name.startswith(expected) for name in names), expected)
        for previous, current in pairs:
            with self.subTest(migration=current):
                self.round_trip('workbench', previous)
                applied = MigrationLoader(connection).applied_migrations
                self.assertIn(('workbench', current), applied)

    def test_commerce_0036(self):
        self.round_trip('commerce', '0035')

    def test_backend_0016(self):
        self.round_trip('backend', '0015')
