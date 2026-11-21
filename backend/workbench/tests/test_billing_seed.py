"""seeds/billing.py runs twice without duplicating rows and backs the billing/org screens."""
from django.core.management import call_command

from backend.commerce.models import Department, DepartmentMember, Reservation
from backend.models import AuditEvent
from backend.workbench.models import AuthorizationRequest
from backend.workbench.tests.base import AppTestCase


class BillingSeedTests(AppTestCase):
    def counts(self):
        return (Department.objects.count(), DepartmentMember.objects.count(), Reservation.objects.count(),
                AuditEvent.objects.filter(request_id__isnull=False).count(), AuthorizationRequest.objects.count())

    def test_idempotent(self):
        call_command('seed_app', only=['core', 'billing'], stdout=open('/dev/null', 'w'))
        first = self.counts()
        call_command('seed_app', only=['core', 'billing'], stdout=open('/dev/null', 'w'))
        self.assertEqual(self.counts(), first)
        self.assertEqual(set(Department.objects.values_list('name', flat=True)) >= {'运营部', '市场部'}, True)
        self.assertTrue(AuditEvent.objects.filter(summary='市场部 3,000 → 4,000').exists())
