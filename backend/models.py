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
    identifier = models.CharField(max_length=255)
    # Google assertion metadata, never an original email login/recovery identity.
    provider_email = models.EmailField(max_length=254, blank=True)
    verified_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['method', 'identifier'], name='verified_identity_unique')]


class LoginChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=20, default='login',
        choices=[('login', 'login'), ('password_reset', 'password_reset'), ('registration', 'registration'),
            ('google_bind','google_bind')])
    password_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='+')
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


class AuthFlow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    method = models.CharField(max_length=10, default='email')
    purpose = models.CharField(max_length=16, choices=[('registration', 'registration'),
        ('login', 'login'), ('recovery', 'recovery'), ('google', 'google')])
    stage = models.CharField(max_length=12, default='captcha')
    identifier = models.CharField(max_length=254)
    session_digest = models.CharField(max_length=64, db_index=True)
    password_hash = models.CharField(max_length=128, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    agreement_version = models.CharField(max_length=128, blank=True)
    agreement_accepted_at = models.DateTimeField(null=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    challenge = models.OneToOneField(LoginChallenge, null=True, on_delete=models.SET_NULL)
    captcha_digest = models.CharField(max_length=64, null=True, unique=True)
    google_state_digest = models.CharField(max_length=64, null=True, unique=True)
    google_nonce_digest = models.CharField(max_length=64, blank=True)
    google_claim = models.CharField(max_length=64, blank=True)
    google_sub = models.CharField(max_length=255, blank=True)
    original_identity = models.ForeignKey(LoginIdentity, null=True, on_delete=models.SET_NULL)
    return_to = models.CharField(max_length=2048, blank=True)
    language = models.CharField(max_length=2, default='zh')
    google_intent = models.CharField(max_length=8, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class InvitationCodeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs) - {'is_active'}:
            raise ValueError('IMMUTABLE_INVITATION_MAPPING')
        return super().update(**kwargs)

    def delete(self):
        raise ValueError('IMMUTABLE_INVITATION_MAPPING')

    def bulk_update(self, objs, fields, batch_size=None):
        if set(fields) - {'is_active'}:
            raise ValueError('IMMUTABLE_INVITATION_MAPPING')
        return super().bulk_update(objs, fields, batch_size)

    def bulk_create(self, *args, **kwargs):
        # The narrow importer validates and writes each permanently owned code.
        raise ValueError('IMMUTABLE_INVITATION_MAPPING')


class InvitationCode(models.Model):
    objects = InvitationCodeQuerySet.as_manager()
    code = models.CharField(max_length=16, primary_key=True)
    blogger = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        constraints = [models.CheckConstraint(condition=models.Q(code__regex=r'^[A-Z0-9]{6,16}$'),
                                               name='invitation_code_ascii')]

    def save(self, *args, **kwargs):
        from .invitations import normalize_code
        self.code = normalize_code(self.code)
        if not self.code or not isinstance(self.blogger, str) or not self.blogger.strip() or len(self.blogger) > 160:
            raise ValueError('INVALID_INVITATION_MAPPING')
        if any(ord(char) < 32 for char in self.blogger):
            raise ValueError('INVALID_INVITATION_MAPPING')
        original = type(self).objects.filter(pk=self.code).first()
        if original and original.blogger != self.blogger:
            raise ValueError('IMMUTABLE_INVITATION_MAPPING')
        if not self._state.adding and self.code != self._loaded_code:
            raise ValueError('IMMUTABLE_INVITATION_MAPPING')
        if self._state.adding:
            if args or kwargs.get('force_update') or kwargs.get('update_fields') is not None:
                raise ValueError('IMMUTABLE_INVITATION_MAPPING')
            # An existence read can become stale before save. Never let Django
            # fall back to UPDATE for a new instance with a supplied code PK.
            kwargs['force_insert'] = True
        result = super().save(*args, **kwargs)
        self._loaded_code = self.code
        return result

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_code = instance.pk
        return instance

    def delete(self, *args, **kwargs):
        raise ValueError('IMMUTABLE_INVITATION_MAPPING')


class RegistrationAttributionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')

    def delete(self):
        raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')

    def bulk_create(self, *args, **kwargs):
        raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')


class RegistrationAttribution(models.Model):
    objects = RegistrationAttributionQuerySet.as_manager()
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    invitation_code = models.ForeignKey(InvitationCode, on_delete=models.PROTECT)
    registered_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        verbose_name = '邀请码归因记录'
        verbose_name_plural = '邀请码归因记录'
        permissions = [('view_registration_attribution', '查看邀请码归因记录'),
                       ('export_registration_attribution', '导出邀请码归因记录')]

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.pk and type(self).objects.filter(pk=self.pk).exists()):
            raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')
        if args or kwargs.get('force_update') or kwargs.get('update_fields') is not None:
            raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('IMMUTABLE_REGISTRATION_ATTRIBUTION')


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
    product_updates = models.BooleanField(default=True)
    preference_changed_at = models.DateTimeField(null=True)
    agreement_version = models.CharField(max_length=128, blank=True)
    agreement_accepted_at = models.DateTimeField(null=True)


class AccountSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=40, unique=True)
    client = models.CharField(max_length=100)
    ip = models.GenericIPAddressField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True)


class MemberRemovalQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('IMMUTABLE_MEMBER_REMOVAL')

    def delete(self):
        raise ValueError('IMMUTABLE_MEMBER_REMOVAL')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('IMMUTABLE_MEMBER_REMOVAL')


class MemberRemoval(models.Model):
    objects = MemberRemovalQuerySet.as_manager()
    account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT)
    membership = models.OneToOneField(Membership, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_key = models.CharField(max_length=64)
    handoff_pending = models.BooleanField(default=False)
    pending_reservation_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'request_key'], name='member_removal_request_unique')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            if any(getattr(self, field.attname) != getattr(original, field.attname) for field in self._meta.concrete_fields):
                raise ValueError('IMMUTABLE_MEMBER_REMOVAL')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('IMMUTABLE_MEMBER_REMOVAL')


class AccountDeletionChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    identifier_digest = models.CharField(max_length=64)
    session_digest = models.CharField(max_length=64)
    code_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    delivered = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True)


class TrialIdentityMarker(models.Model):
    method = models.CharField(max_length=10)
    identifier_digest = models.CharField(max_length=64)
    key_fingerprint = models.CharField(max_length=64)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['method', 'identifier_digest'], name='trial_identity_marker_unique')]


class AccountDeletionRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default='requested', choices=[('requested', '待处理'), ('reviewing', '核验中'), ('waiting_user', '待用户处理'), ('delayed', '延期'), ('completed', '已注销')])
    policy_version = models.CharField(max_length=80)
    blockers = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    first_response_due_at = models.DateTimeField()
    completion_due_at = models.DateTimeField()
    first_responded_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    retain_until = models.DateTimeField()
    delay_reason = models.CharField(max_length=1000, blank=True)
    expected_completion_at = models.DateTimeField(null=True, blank=True)
    progress_message = models.CharField(max_length=1000, blank=True)

    class Meta:
        permissions = [('manage_account_deletion', '核验和处理账号注销申请')]


class AccountDeletionAudit(models.Model):
    request = models.ForeignKey(AccountDeletionRequest, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)


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
