"""Trial persistence fixtures; none execute a task or fetch a storefront."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import close_old_connections, connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from backend.models import BillingAccount, LoginIdentity, Membership, TrialAttempt, TrialEvent, TrialIdentityMarker
from backend.trials import (
    read_trial, record_trial_event, start_trial, update_trial, validate_trial_input,
)


REPORT = {"title": "Explicit test fixture", "sections": [{"finding": "Fixture only"}]}


@override_settings(ACCOUNT_DELETION_MARKER_KEY='isolated-trial-marker-fixture-' * 3)
class ClosedAccountTrialTests(TransactionTestCase):
    def setUp(self):
        from backend.account import trial_identifier_digest
        self.user = get_user_model().objects.create_user(username='replacement-fixture')
        LoginIdentity.objects.create(user=self.user, method='email', identifier='replacement@example.test')
        self.marker = TrialIdentityMarker.objects.create(method='email',
            identifier_digest=trial_identifier_digest('email', 'replacement@example.test'),
            key_fingerprint=hashlib.sha256(settings.ACCOUNT_DELETION_MARKER_KEY.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=1))

    def test_re_registered_identity_cannot_repeat_trial(self):
        self.assertEqual(read_trial(self.user), {'eligibility': 'consumed', 'attempt': None})
        with self.assertRaisesRegex(ValueError, 'TRIAL_ALREADY_USED'):
            start_trial(self.user, 'voc', 'Fixture brand', 'fixture-repeat')
        self.assertFalse(TrialAttempt.objects.filter(user=self.user).exists())

    def test_expired_marker_does_not_block_trial(self):
        self.marker.expires_at = timezone.now() - timedelta(seconds=1)
        self.marker.save(update_fields=['expires_at'])
        self.assertEqual(read_trial(self.user)['eligibility'], 'available')
        self.assertEqual(start_trial(self.user, 'voc', 'Fixture brand', 'fixture-expired')['eligibility'], 'locked')

    def test_other_identity_not_blocked(self):
        other = get_user_model().objects.create_user(username='different-fixture')
        LoginIdentity.objects.create(user=other, method='email', identifier='different@example.test')
        self.assertEqual(read_trial(other)['eligibility'], 'available')

    def test_missing_marker_key_returns_explicit_unavailable(self):
        self.client.force_login(self.user)
        with override_settings(ACCOUNT_DELETION_MARKER_KEY=''):
            response = self.client.get('/api/trial')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'RETENTION_POLICY_PENDING')


def digest(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class TrialInputTests(SimpleTestCase):
    def test_brand_normalization_does_not_fetch_or_execute_input(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS allowed")):
            normalized, key = validate_trial_input("voc", "  品牌 Sage  ")
        self.assertEqual(normalized, "品牌 Sage")
        self.assertEqual(len(key), 64)
        self.assertEqual(validate_trial_input("voc", "Cafe\u0301"), validate_trial_input("voc", "Café"))

    def test_invalid_brand_and_scenario(self):
        for value in (None, True, {}, "", "   ", "x" * 201, "line\nline", "\x00brand", "http://store.test", "品牌\u202e"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "INVALID_TRIAL_INPUT"):
                validate_trial_input("voc", value)
        with self.assertRaisesRegex(ValueError, "INVALID_TRIAL_SCENARIO"):
            validate_trial_input("not-a-trial", "brand")

    def test_https_public_hostname_is_syntax_validation_not_store_verification(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS allowed")):
            value, key = validate_trial_input("shopify_diagnosis", "https://Fixture-Store.myshopify.com/")
            custom, _ = validate_trial_input("shopify_diagnosis", "https://shop.fixture-brand.com")
        self.assertEqual(value, "https://fixture-store.myshopify.com/")
        self.assertEqual(custom, "https://shop.fixture-brand.com/")
        self.assertEqual(len(key), 64)

    def test_shopify_input_rejects_unsafe_or_ambiguous_url_shapes(self):
        values = [
            "http://store.myshopify.com", "file:///etc/passwd", "//store.myshopify.com",
            "https://localhost", "https://store.local", "https://store.internal",
            "https://metadata.google.internal", "https://169.254.169.254/",
            "https://127.0.0.1", "https://127.1", "https://2130706433",
            "https://0x7f000001", "https://[::1]", "https://[2606:4700:4700::1111]",
            "https://user:pass@store.myshopify.com", "https://store.myshopify.com:443",
            "https://store.myshopify.com:8080", "https://store.myshopify.com./",
            "https://store.myshopify.com\\@localhost/", "https://store.myshopify.com/%2e%2e/",
            "https://store.myshopify.com/?next=http://localhost", "https://store.myshopify.com/#token",
            "https://store.myshopify.com\n/", "https://foo..com", "https://-foo.com",
            "https://foo%2ecom", "https://store.invalid", "https://foo.onion",
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "INVALID_TRIAL_INPUT"):
                validate_trial_input("shopify_diagnosis", value)


class TrialPersistenceTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="trial-fixture")
        self.other = get_user_model().objects.create_user(username="other-trial-fixture")

    def start(self, key="first"):
        return start_trial(self.user, "voc", "Fixture brand", key)

    def test_http_read_requires_login_and_never_creates_or_completes_tasks(self):
        self.assertEqual(self.client.get('/api/trial').status_code, 401)
        self.client.force_login(self.user)
        response = self.client.get('/api/trial')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'eligibility': 'available', 'attempt': None})
        self.assertEqual(TrialAttempt.objects.count(), 0)
        attempt = self.start()['attempt']
        self.assertEqual(self.client.get('/api/trial').json()['attempt']['id'], attempt['id'])
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, method)('/api/trial').status_code, 405)
        self.assertEqual(TrialAttempt.objects.get(pk=attempt['id']).task_state, attempt['task_state'])
        self.complete(attempt['id'])
        for _ in range(2):
            result = self.client.get('/api/trial').json()
            self.assertEqual(result['eligibility'], 'consumed')
            self.assertEqual(result['attempt']['report'], REPORT)
        self.assertEqual(TrialAttempt.objects.count(), 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/api/trial').json()['attempt'], None)
        self.assertEqual(self.client.get('/api/trial', {'attempt_id': attempt['id']}).status_code, 422)

    def complete(self, attempt_id, key="report-ready", report=None):
        report = REPORT if report is None else report
        return record_trial_event(self.user, attempt_id, key, "complete", report=report, evidence_hash=digest(report))

    def test_invalid_input_never_creates_or_locks_eligibility(self):
        with self.assertRaisesRegex(ValueError, "INVALID_TRIAL_INPUT"):
            start_trial(self.user, "voc", "", "invalid")
        self.assertEqual(read_trial(self.user), {"eligibility": "available", "attempt": None})
        self.assertFalse(TrialAttempt.objects.exists())

    def test_start_replay_and_same_object_resume_do_not_create_extra_attempts(self):
        first = self.start()
        self.assertEqual(first["eligibility"], "locked")
        self.assertEqual(first, self.start())
        self.assertEqual(first, self.start("new-request-same-object"))
        self.assertEqual(TrialAttempt.objects.count(), 1)
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            start_trial(self.user, "voc", "Changed brand", "first")
        with self.assertRaisesRegex(ValueError, "TRIAL_ORIGINAL_TASK_REQUIRED"):
            start_trial(self.user, "shopify_diagnosis", "https://store.myshopify.com/", "second")

    def test_read_and_other_user_cannot_mutate_or_view_another_attempt(self):
        attempt_id = self.start()["attempt"]["id"]
        self.assertEqual(read_trial(self.other)["eligibility"], "available")
        operations = [
            lambda: read_trial(self.other, attempt_id),
            lambda: update_trial(self.other, attempt_id, "cancel", "cancel"),
            lambda: update_trial(self.other, attempt_id, "retry", "retry"),
            lambda: record_trial_event(self.other, attempt_id, "report", "complete", report=REPORT, evidence_hash=digest(REPORT)),
        ]
        for operation in operations:
            with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
                operation()
        self.assertEqual(read_trial(self.user)["attempt"]["task_state"], "running")
        self.assertFalse(TrialEvent.objects.exists())

    def test_cancel_disconnect_and_recoverable_failure_keep_same_object_locked(self):
        attempt_id = self.start()["attempt"]["id"]
        self.assertEqual(read_trial(self.user)["attempt"]["id"], attempt_id)
        cancelled = update_trial(self.user, attempt_id, "cancel", "cancel-1")
        self.assertEqual(cancelled["eligibility"], "locked")
        self.assertEqual(cancelled["attempt"]["task_state"], "cancelled")
        retried = update_trial(self.user, attempt_id, "retry", "retry-1")
        self.assertEqual(retried["attempt"]["task_state"], "running")
        failed = record_trial_event(self.user, attempt_id, "failure-1", "recoverable_failure", evidence_hash="a" * 64)
        self.assertEqual(failed["eligibility"], "locked")
        self.assertEqual(failed["attempt"]["task_state"], "recoverable_failure")
        self.assertEqual(update_trial(self.user, attempt_id, "retry", "retry-2")["attempt"]["id"], attempt_id)
        with self.assertRaisesRegex(ValueError, "TRIAL_ORIGINAL_TASK_REQUIRED"):
            start_trial(self.user, "voc", "Different", "different")

    def test_user_actions_cannot_report_success_or_release_eligibility(self):
        attempt_id = self.start()["attempt"]["id"]
        for action in ("complete", "irrecoverable_failure", "recoverable_failure"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "INVALID_TRIAL_ACTION"):
                update_trial(self.user, attempt_id, action, action)
        self.assertEqual(read_trial(self.user)["eligibility"], "locked")

    def test_completion_requires_report_and_matching_content_evidence(self):
        attempt_id = self.start()["attempt"]["id"]
        for report in (None, {}, [], True, {"number": float("nan")}):
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, "TRIAL_RESULT_NOT_READY"):
                record_trial_event(self.user, attempt_id, "complete", "complete", report=report, evidence_hash="a" * 64)
        with self.assertRaisesRegex(ValueError, "TRIAL_EVIDENCE_MISMATCH"):
            record_trial_event(self.user, attempt_id, "complete", "complete", report=REPORT, evidence_hash="a" * 64)
        self.assertEqual(read_trial(self.user)["eligibility"], "locked")
        self.assertFalse(TrialEvent.objects.exists())

    def test_complete_persists_owner_viewable_report_and_consumes_once(self):
        attempt_id = self.start()["attempt"]["id"]
        result = self.complete(attempt_id)
        self.assertEqual(result["eligibility"], "consumed")
        self.assertEqual(read_trial(self.user, attempt_id)["attempt"]["report"], REPORT)
        self.assertEqual(result, self.complete(attempt_id))
        self.assertEqual(TrialEvent.objects.count(), 1)
        with self.assertRaisesRegex(ValueError, "TRIAL_ALREADY_USED"):
            self.start("second-start")
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            self.complete(attempt_id, report={"title": "different fixture"})
        with self.assertRaisesRegex(ValueError, "INVALID_TRIAL_TRANSITION"):
            update_trial(self.user, attempt_id, "cancel", "late-cancel")

    def test_report_event_and_consumption_are_one_atomic_commit(self):
        attempt_id = self.start()["attempt"]["id"]
        with patch("backend.trials.TrialEvent.objects.create", side_effect=RuntimeError("Fixture storage failure")):
            with self.assertRaisesRegex(RuntimeError, "Fixture storage failure"):
                self.complete(attempt_id)
        result = read_trial(self.user)
        self.assertEqual(result["eligibility"], "locked")
        self.assertIsNone(result["attempt"]["report"])
        self.assertFalse(TrialEvent.objects.exists())

    def test_only_trusted_irrecoverable_failure_releases_and_old_replay_cannot_unlock_new_attempt(self):
        first = self.start()["attempt"]["id"]
        with self.assertRaisesRegex(ValueError, "TRIAL_EVIDENCE_REQUIRED"):
            record_trial_event(self.user, first, "failure", "irrecoverable_failure")
        failed = record_trial_event(self.user, first, "failure", "irrecoverable_failure", evidence_hash="b" * 64)
        self.assertEqual(failed["eligibility"], "available")
        self.assertEqual(self.start()["attempt"]["id"], first)
        second = start_trial(self.user, "shopify_diagnosis", "https://fixture-store.myshopify.com", "new-after-failure")
        second_id = second["attempt"]["id"]
        self.assertNotEqual(second_id, first)
        record_trial_event(self.user, first, "failure", "irrecoverable_failure", evidence_hash="b" * 64)
        self.assertEqual(read_trial(self.user)["attempt"]["id"], second_id)
        self.assertEqual(read_trial(self.user)["eligibility"], "locked")
        with self.assertRaisesRegex(ValueError, "STALE_TRIAL_ATTEMPT"):
            record_trial_event(self.user, first, "late-failure", "irrecoverable_failure", evidence_hash="b" * 64)
        with self.assertRaisesRegex(ValueError, "STALE_TRIAL_ATTEMPT"):
            self.complete(first)

    def test_event_replay_does_not_repeat_or_roll_back_later_state(self):
        attempt_id = self.start()["attempt"]["id"]
        update_trial(self.user, attempt_id, "cancel", "cancel")
        update_trial(self.user, attempt_id, "retry", "retry")
        replay = update_trial(self.user, attempt_id, "cancel", "cancel")
        self.assertEqual(replay["attempt"]["task_state"], "running")
        self.assertEqual(TrialEvent.objects.count(), 2)
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            update_trial(self.user, attempt_id, "retry", "cancel")

    def test_added_contact_or_enterprise_membership_does_not_reset_user_trial(self):
        self.complete(self.start()["attempt"]["id"])
        LoginIdentity.objects.create(user=self.user, method="email", identifier="fixture@example.invalid")
        organization = BillingAccount.objects.create(kind="business", name="Fixture organization")
        Membership.objects.create(account=organization, user=self.user, role="owner")
        self.assertEqual(read_trial(self.user)["eligibility"], "consumed")
        with self.assertRaisesRegex(ValueError, "TRIAL_ALREADY_USED"):
            self.start("after-contact-change")

    def test_inactive_user_and_invalid_ids_do_not_disclose_attempt(self):
        self.start()
        for identifier in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
            with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
                read_trial(self.user, identifier)
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaisesRegex(ValueError, "FORBIDDEN"):
            read_trial(self.user)


class TrialConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="parallel-trial-fixture")

    def parallel(self, operations):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency requires PostgreSQL; SQLite is not evidence.")
        barrier = Barrier(len(operations))

        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            except ValueError as exc:
                return str(exc)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            return list(executor.map(run, operations))

    def test_two_distinct_starts_compete_for_one_user_eligibility(self):
        results = self.parallel([
            lambda: start_trial(self.user, "voc", "Fixture one", "one"),
            lambda: start_trial(self.user, "shopify_diagnosis", "https://fixture.myshopify.com", "two"),
        ])
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("TRIAL_ORIGINAL_TASK_REQUIRED", results)
        self.assertEqual(TrialAttempt.objects.count(), 1)

    def test_replayed_start_and_report_are_single_persistent_records(self):
        results = self.parallel([lambda: start_trial(self.user, "voc", "Fixture", "one")] * 2)
        self.assertEqual(results[0], results[1])
        attempt_id = results[0]["attempt"]["id"]
        completions = self.parallel([lambda: record_trial_event(self.user, attempt_id, "report", "complete", report=REPORT, evidence_hash=digest(REPORT))] * 2)
        self.assertEqual(completions[0], completions[1])
        self.assertEqual((TrialAttempt.objects.count(), TrialEvent.objects.count()), (1, 1))
        self.assertEqual(read_trial(self.user)["eligibility"], "consumed")
