import uuid

from django.conf import settings
from django.db import models


class BillingAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=[('personal', 'personal'), ('business', 'business')])
    name = models.CharField(max_length=120)
    personal_owner = models.OneToOneField(settings.AUTH_USER_MODEL, null=True,
                                         on_delete=models.PROTECT, related_name='personal_account')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(kind='personal', personal_owner__isnull=False) |
            models.Q(kind='business', personal_owner__isnull=True)), name='billing_account_owner_kind')]


class Membership(models.Model):
    account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    role = models.CharField(max_length=20, choices=[('owner', 'owner'),
                           ('billing_admin', 'billing_admin'), ('member', 'member')])
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'user'], name='billing_membership_unique')]


class LoginIdentity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    method = models.CharField(max_length=10)
    identifier = models.CharField(max_length=254)
    verified_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['method', 'identifier'], name='verified_identity_unique')]


class LoginChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    method = models.CharField(max_length=10)
    identifier = models.CharField(max_length=254)
    code_digest = models.CharField(max_length=64)
    session_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    delivered = models.BooleanField(default=False)
    consumed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AuthThrottle(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window_at = models.DateTimeField()
    last_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)


class PurchaseInquiry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    company = models.CharField(max_length=160)
    seats = models.PositiveIntegerField()
    requirements = models.TextField()
    contact_email = models.EmailField()
    status = models.CharField(max_length=20, default='received')
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    account = models.ForeignKey(BillingAccount, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=60)
    request_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)


class TrialAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    scenario = models.CharField(max_length=32)
    input_value = models.CharField(max_length=2048)
    input_key = models.CharField(max_length=64)
    request_key = models.CharField(max_length=128)
    task_state = models.CharField(max_length=32, default='running')
    report = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'request_key'], name='trial_request_unique'),
            models.UniqueConstraint(fields=['user'], condition=~models.Q(task_state='irrecoverable_failure'),
                                    name='trial_one_unreleased_attempt'),
            models.CheckConstraint(condition=models.Q(task_state__in=[
                'running', 'cancelled', 'recoverable_failure', 'completed', 'irrecoverable_failure']),
                name='trial_state_valid'),
        ]


class TrialEvent(models.Model):
    attempt = models.ForeignKey(TrialAttempt, on_delete=models.PROTECT, related_name='events')
    event_key = models.CharField(max_length=128)
    kind = models.CharField(max_length=32)
    request_hash = models.CharField(max_length=64)
    evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['attempt', 'event_key'], name='trial_event_unique')]


class AccountProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    avatar_name = models.CharField(max_length=255, blank=True)
    pending_avatar_deletions = models.JSONField(default=list)
    product_updates = models.BooleanField(default=False)
    preference_changed_at = models.DateTimeField(null=True)


class AccountSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=40, unique=True)
    client = models.CharField(max_length=100)
    ip = models.GenericIPAddressField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True)


class Consultation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.CharField(max_length=160)
    phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True)
    question = models.TextField()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=[('pending', '待处理'),
        ('in_progress', '跟进中'), ('completed', '已完成')], default='pending')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    purge_at = models.DateTimeField(null=True, blank=True, db_index=True)
    purged_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=0)
    request_key = models.CharField(max_length=64, unique=True)
    request_hash = models.CharField(max_length=64)

    class Meta:
        permissions = [('manage_consultation', 'Manage sales consultations'),
                       ('purge_consultation', 'Purge consultation personal information')]
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=['pending', 'in_progress', 'completed']),
                                   name='consultation_status_valid'),
            models.CheckConstraint(condition=(
                models.Q(status='completed', completed_at__isnull=False, purge_at__isnull=False) |
                (models.Q(status__in=['pending', 'in_progress']) &
                 models.Q(completed_at__isnull=True, purge_at__isnull=True))),
                name='consultation_completion_deadline'),
        ]


class ConsultationAudit(models.Model):
    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    request_id = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
