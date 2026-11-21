"""Contract: SAGE_SEED_NOW pins the runtime clock in dev/test; production ignores it."""
import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from backend.workbench import clock

PINNED = '2026-11-19T10:53:00+08:00'


class ClockTests(SimpleTestCase):
    def test_pinned_in_development(self):
        with mock.patch.dict(os.environ, {'SAGE_SEED_NOW': PINNED}):
            with override_settings(ENVIRONMENT='development'):
                self.assertEqual(clock.now().isoformat(), PINNED)
                self.assertEqual(str(clock.localdate()), '2026-11-19')

    def test_pinned_in_test(self):
        with mock.patch.dict(os.environ, {'SAGE_SEED_NOW': PINNED}):
            with override_settings(ENVIRONMENT='test'):
                self.assertEqual(clock.now().isoformat(), PINNED)

    def test_production_ignores_override(self):
        from django.utils import timezone
        with mock.patch.dict(os.environ, {'SAGE_SEED_NOW': PINNED}):
            with override_settings(ENVIRONMENT='production'):
                delta = abs((clock.now() - timezone.now()).total_seconds())
                self.assertLess(delta, 5)

    def test_unset_uses_real_clock(self):
        from django.utils import timezone
        with mock.patch.dict(os.environ, {'SAGE_SEED_NOW': ''}):
            with override_settings(ENVIRONMENT='development'):
                delta = abs((clock.now() - timezone.now()).total_seconds())
                self.assertLess(delta, 5)
