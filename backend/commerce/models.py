import uuid

from django.conf import settings
from django.db import models


def _unchanged(instance, fields):
    if instance._state.adding:
        return
    original = type(instance).objects.filter(pk=instance.pk).values(*fields).get()
    if any(getattr(instance, field) != value for field, value in original.items()):
        raise ValueError("IMMUTABLE_BILLING_SNAPSHOT")


class ProductVersion(models.Model):
    """No production products are seeded: commercial values await approval."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80)
    version = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=[("subscription", "subscription"), ("credits", "credits")])
    audience = models.CharField(max_length=10, choices=[("personal", "personal"), ("business", "business")])
    billing_cycle = models.CharField(max_length=10, blank=True)
    currency = models.CharField(max_length=3, default="CNY")
    unit_amount_minor = models.PositiveBigIntegerField(null=True)
    credits_quantity = models.TextField(null=True)
    minimum_seats = models.PositiveIntegerField(default=1)
    entitlements = models.JSONField(default=dict)
    entitlement_version = models.CharField(max_length=80, blank=True)
    credit_rule_version = models.CharField(max_length=80, blank=True)
    terms_version = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=12, default="draft")
    effective_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="commerce_product_version_unique")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            state = type(self).objects.values_list("state", flat=True).get(pk=self.pk)
            if state != "draft":
                if self.state == "draft":
                    raise ValueError("IMMUTABLE_BILLING_SNAPSHOT")
                _unchanged(self, [field.attname for field in self._meta.concrete_fields if field.name not in {"state", "created_at"}])
        return super().save(*args, **kwargs)


class Quote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("backend.BillingAccount", on_delete=models.PROTECT)
    product = models.ForeignKey(ProductVersion, on_delete=models.PROTECT)
    snapshot = models.JSONField()
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        _unchanged(self, [field.attname for field in self._meta.concrete_fields if field.name != "created_at"])
        return super().save(*args, **kwargs)


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("backend.BillingAccount", on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    quote = models.OneToOneField(Quote, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    snapshot = models.JSONField()
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=32, default="pending_payment")
    paid_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "idempotency_key"], name="commerce_order_idempotency_unique")]
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        _unchanged(self, ["account_id", "created_by_id", "quote_id", "idempotency_key", "request_hash", "snapshot", "amount_minor", "currency"])
        return super().save(*args, **kwargs)


class PaymentAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("backend.BillingAccount", on_delete=models.PROTECT)
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    intent_key = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=12, default="created")
    dispatched_at = models.DateTimeField(null=True)
    dispatch_authorization = models.JSONField(null=True)
    needs_reconciliation = models.BooleanField(default=False)
    subscription = models.ForeignKey('Subscription', null=True, on_delete=models.PROTECT)
    renewal_cycle_index = models.PositiveIntegerField(null=True)
    dispatch_not_before = models.DateTimeField(null=True)
    recovery_of = models.OneToOneField('self', null=True, on_delete=models.PROTECT, related_name='recovery_attempt')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "intent_key"], name="commerce_payment_intent_unique"),
            models.UniqueConstraint(fields=["subscription", "renewal_cycle_index"], name="commerce_renewal_intent_unique"),
            models.CheckConstraint(condition=(models.Q(recovery_of__isnull=True) |
                (models.Q(subscription__isnull=True) & ~models.Q(recovery_of=models.F('id')))),
                name='commerce_recovery_binding_valid'),
            models.CheckConstraint(condition=(
                models.Q(subscription__isnull=True, renewal_cycle_index__isnull=True, dispatch_not_before__isnull=True) |
                models.Q(subscription__isnull=False, renewal_cycle_index__isnull=False, renewal_cycle_index__gt=0,
                         dispatch_not_before__isnull=False, expires_at__gt=models.F('dispatch_not_before'))
            ), name="commerce_renewal_binding_valid"),
            models.CheckConstraint(condition=models.Q(state__in=["created", "pending", "unknown", "succeeded", "failed", "cancelled", "expired", "review"]), name="commerce_attempt_state_valid"),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ["account_id", "order_id", "intent_key", "expires_at", "subscription_id",
                          "renewal_cycle_index", "dispatch_not_before", "recovery_of_id"])
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            if original.dispatched_at is not None or original.dispatch_authorization is not None:
                _unchanged(self, ['dispatched_at', 'dispatch_authorization'])
        return super().save(*args, **kwargs)


class PaymentAttemptEvent(models.Model):
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="events")
    event_key = models.CharField(max_length=128)
    source = models.CharField(max_length=20)
    outcome = models.CharField(max_length=12)
    request_hash = models.CharField(max_length=64)
    evidence_hash = models.CharField(max_length=64, blank=True)
    applied = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "event_key"], name="commerce_attempt_event_unique")]

    def save(self, *args, **kwargs):
        _unchanged(self, [field.attname for field in self._meta.concrete_fields if field.name != "created_at"])
        return super().save(*args, **kwargs)


class PaymentFact(models.Model):
    """Verified receipts only; there is deliberately no public write endpoint."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payment_facts")
    channel = models.CharField(max_length=40)
    merchant_reference = models.CharField(max_length=128)
    transaction_reference = models.CharField(max_length=128)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    evidence_hash = models.CharField(max_length=64)
    disposition = models.CharField(max_length=32)
    paid_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "merchant_reference", "transaction_reference"], name="commerce_receipt_unique")]

    def save(self, *args, **kwargs):
        _unchanged(self, [field.attname for field in self._meta.concrete_fields if field.name != "created_at"])
        return super().save(*args, **kwargs)


class PaymentMethod(models.Model):
    """Account-scoped, non-chargeable instrument fingerprint; never card data."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    channel = models.CharField(max_length=128)
    merchant_reference = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    evidence_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=10, default='active')
    removal_requested_at = models.DateTimeField(null=True)
    removed_at = models.DateTimeField(null=True)
    removal_evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'channel', 'merchant_reference', 'fingerprint'],
                                    name='commerce_method_identity_unique'),
            models.CheckConstraint(condition=models.Q(state__in=['active', 'removing', 'removed']),
                                   name='commerce_method_state_valid'),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ['account_id', 'channel', 'merchant_reference', 'fingerprint', 'evidence_hash'])
        return super().save(*args, **kwargs)


class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("backend.BillingAccount", on_delete=models.PROTECT)
    initial_order = models.OneToOneField(Order, on_delete=models.PROTECT)
    original_anchor = models.DateTimeField()
    billing_timezone = models.CharField(max_length=64)
    calendar_rule_version = models.CharField(max_length=80)
    billing_cycle = models.CharField(max_length=10)
    mandate = models.JSONField()
    authorization_version = models.PositiveIntegerField(default=0)
    payment_method = models.ForeignKey(PaymentMethod, null=True, on_delete=models.PROTECT)
    auto_renew = models.BooleanField(default=True)
    cancellation_state = models.CharField(max_length=12, default="none")
    cancel_requested_at = models.DateTimeField(null=True)
    cancel_confirmed_at = models.DateTimeField(null=True)
    cancellation_evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account"], condition=models.Q(auto_renew=True),
                                    name="commerce_one_renewing_subscription"),
            models.CheckConstraint(condition=models.Q(billing_cycle__in=["monthly", "annual"]),
                                   name="commerce_subscription_cycle_valid"),
            models.CheckConstraint(condition=(models.Q(auto_renew=True, cancellation_state="none") |
                                   models.Q(auto_renew=False, cancellation_state__in=["processing", "completed"])),
                                   name="commerce_subscription_cancel_valid"),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ["account_id", "initial_order_id", "original_anchor", "billing_timezone",
                          "calendar_rule_version", "billing_cycle", "mandate", "payment_method_id", "authorization_version"])
        return super().save(*args, **kwargs)


class AuthorizationChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name='authorization_changes')
    version = models.PositiveIntegerField()
    request_key = models.CharField(max_length=128)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    old_mandate = models.JSONField()
    new_mandate = models.JSONField()
    old_method = models.ForeignKey(PaymentMethod, null=True, on_delete=models.PROTECT, related_name='+')
    new_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name='+')
    revoked_at = models.DateTimeField(null=True)
    revocation_evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['subscription', 'version'], name='commerce_auth_change_version'),
            models.UniqueConstraint(fields=['subscription', 'request_key'], name='commerce_auth_change_request'),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ['subscription_id', 'version', 'request_key', 'actor_id',
                         'old_mandate', 'new_mandate', 'old_method_id', 'new_method_id'])
        if not self._state.adding and type(self).objects.filter(pk=self.pk, revoked_at__isnull=False).exists():
            _unchanged(self, ['revoked_at', 'revocation_evidence_hash'])
        return super().save(*args, **kwargs)


class TopupAuthorization(models.Model):
    """Independent consent history. A recorded consent never starts a charge."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT)
    product = models.ForeignKey(ProductVersion, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    request_key = models.CharField(max_length=128)
    configuration = models.JSONField()
    mandate = models.JSONField()
    state = models.CharField(max_length=12, default='authorized')
    pause_reason = models.CharField(max_length=64, blank=True)
    revocation_requested_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    revocation_evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account','version'], name='commerce_topup_version'),
            models.UniqueConstraint(fields=['account','request_key'], name='commerce_topup_request'),
            models.CheckConstraint(condition=models.Q(state__in=['authorized','paused','superseded','revoking','revoked']),
                                   name='commerce_topup_state'),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ['account_id','actor_id','payment_method_id','product_id','version',
                         'request_key','configuration','mandate'])
        if not self._state.adding and type(self).objects.filter(pk=self.pk,state='revoked').exists():
            _unchanged(self, ['state','revocation_requested_at','revoked_at','revocation_evidence_hash'])
        return super().save(*args, **kwargs)


class TopupBudget(models.Model):
    """One immutable budget reservation per automatic top-up payment intent."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    authorization = models.ForeignKey(TopupAuthorization, on_delete=models.PROTECT)
    attempt = models.OneToOneField('PaymentAttempt', on_delete=models.PROTECT, related_name='topup_budget')
    trigger_key = models.CharField(max_length=128)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    policy_version = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account','trigger_key'], name='commerce_topup_trigger'),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')),name='commerce_topup_window'),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ['account_id','authorization_id','attempt_id','trigger_key','starts_at','ends_at','policy_version'])
        return super().save(*args, **kwargs)


class SubscriptionPeriod(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="periods")
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    cycle_index = models.PositiveIntegerField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    snapshot = models.JSONField()
    granted_months = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["subscription", "cycle_index"], name="commerce_subscription_period_unique"),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")),
                                   name="commerce_subscription_period_ordered"),
        ]

    def save(self, *args, **kwargs):
        _unchanged(self, ["subscription_id", "order_id", "cycle_index", "starts_at", "ends_at", "snapshot"])
        return super().save(*args, **kwargs)


class PaidSeat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name='seats')
    position = models.PositiveIntegerField()
    membership = models.ForeignKey('backend.Membership', null=True, on_delete=models.PROTECT)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['subscription','position'],name='commerce_paid_seat_unique'),
                       models.UniqueConstraint(fields=['subscription','membership'],condition=models.Q(membership__isnull=False),name='commerce_member_seat_unique'),
                       models.CheckConstraint(condition=models.Q(position__gte=1),name='commerce_paid_seat_positive')]

    def save(self, *args, **kwargs):
        _unchanged(self, ['subscription_id','position'])
        super().save(*args, **kwargs)


class SeatChange(models.Model):
    seat = models.ForeignKey(PaidSeat, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    before_membership = models.ForeignKey('backend.Membership', null=True, on_delete=models.PROTECT, related_name='+')
    after_membership = models.ForeignKey('backend.Membership', null=True, on_delete=models.PROTECT, related_name='+')
    request_key = models.CharField(max_length=128)
    expected_version = models.PositiveIntegerField()
    resulting_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['seat','request_key'],name='commerce_seat_change_unique')]

    def save(self, *args, **kwargs):
        _unchanged(self, ['seat_id','actor_id','before_membership_id','after_membership_id',
                          'request_key','expected_version','resulting_version'])
        super().save(*args, **kwargs)


class CreditBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("backend.BillingAccount", on_delete=models.PROTECT)
    source = models.CharField(max_length=20, choices=[("subscription", "subscription"), ("purchased", "purchased")])
    source_key = models.CharField(max_length=128)
    order = models.OneToOneField(Order, null=True, on_delete=models.PROTECT)
    subscription_period = models.ForeignKey(SubscriptionPeriod, null=True, on_delete=models.PROTECT)
    service_month = models.PositiveSmallIntegerField(null=True)
    seat = models.ForeignKey(PaidSeat, null=True, on_delete=models.PROTECT)
    initial_quantity = models.TextField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "source_key"], name="commerce_credit_source_unique"),
                       models.UniqueConstraint(fields=["subscription_period", "service_month"], condition=models.Q(seat__isnull=True), name="commerce_subscription_month_unique"),
                       models.UniqueConstraint(fields=['subscription_period','service_month','seat'], condition=models.Q(seat__isnull=False),name='commerce_seat_month_unique')]


class CreditFreeze(models.Model):
    """Internal refund allocation; not proof of returned money."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(CreditBatch, on_delete=models.PROTECT)
    request_key = models.CharField(max_length=128)
    quantity = models.TextField()
    state = models.CharField(max_length=12, default='frozen')
    evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'request_key'], name='commerce_credit_freeze_unique'),
                       models.CheckConstraint(condition=models.Q(state__in=['frozen', 'released', 'refunded']), name='commerce_credit_freeze_state')]

    def save(self, *args, **kwargs):
        _unchanged(self, ['batch_id', 'request_key', 'quantity'])
        super().save(*args, **kwargs)


class BillingIssue(models.Model):
    """Internal finding identity. Disappearance from a scan is not resolution."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    code = models.CharField(max_length=64)
    object_type = models.CharField(max_length=32)
    object_id = models.UUIDField()
    state = models.CharField(max_length=12, default='open')
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account','code','object_type','object_id'],name='commerce_billing_issue_identity')]

    def save(self, *args, **kwargs):
        _unchanged(self,['account_id','code','object_type','object_id','first_seen_at'])
        super().save(*args, **kwargs)


class CreditRefund(models.Model):
    """Internal reviewed refund allocation; never proof of a channel call."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(PaymentFact, on_delete=models.PROTECT, related_name='credit_refunds')
    freeze = models.OneToOneField(CreditFreeze, on_delete=models.PROTECT)
    request_key = models.CharField(max_length=128)
    amount_minor = models.PositiveBigIntegerField()
    review_evidence_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=12, default='reserved')
    evidence_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['payment','request_key'],name='commerce_credit_refund_request'),
            models.CheckConstraint(condition=models.Q(amount_minor__gt=0),name='commerce_credit_refund_positive'),
            models.CheckConstraint(condition=models.Q(state__in=['reserved','refunded','released']),name='commerce_credit_refund_state')]

    def save(self, *args, **kwargs):
        _unchanged(self,['payment_id','freeze_id','request_key','amount_minor','review_evidence_hash'])
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(state='reserved').exists():
            _unchanged(self,['state','evidence_hash','resolved_at'])
        super().save(*args, **kwargs)


class Reservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("backend.BillingAccount", on_delete=models.PROTECT)
    task_key = models.CharField(max_length=128)
    membership = models.ForeignKey('backend.Membership', null=True, on_delete=models.PROTECT)
    shared_period_start = models.DateTimeField(null=True)
    shared_period_end = models.DateTimeField(null=True)
    quantity = models.TextField()
    settled_quantity = models.TextField(null=True)
    state = models.CharField(max_length=12, default="reserved")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "task_key"], name="commerce_task_reservation_unique"),
            models.CheckConstraint(condition=(models.Q(membership__isnull=True,shared_period_start__isnull=True,shared_period_end__isnull=True) |
                models.Q(membership__isnull=False,shared_period_start__isnull=False,shared_period_end__isnull=False,shared_period_end__gt=models.F('shared_period_start'))),
                name='commerce_reservation_member_period')]


class SharedLimitChange(models.Model):
    membership = models.ForeignKey('backend.Membership', on_delete=models.PROTECT, related_name='shared_limit_changes')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    quantity = models.TextField(null=True)
    version = models.PositiveIntegerField()
    request_key = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['membership','version'],name='commerce_member_limit_version'),
                       models.UniqueConstraint(fields=['membership','request_key'],name='commerce_member_limit_request'),
                       models.CheckConstraint(condition=models.Q(version__gte=1),name='commerce_member_limit_positive')]

    def save(self, *args, **kwargs):
        _unchanged(self,['membership_id','actor_id','quantity','version','request_key'])
        super().save(*args, **kwargs)


class TaskCostEvent(models.Model):
    """Immutable internal cost facts, never customer Credits debits."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name='cost_events')
    source = models.CharField(max_length=128)
    event_key = models.CharField(max_length=128)
    payload = models.JSONField()
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account','source','event_key'], name='commerce_task_cost_event_unique')]

    def save(self, *args, **kwargs):
        _unchanged(self, ['account_id','reservation_id','source','event_key','payload','request_hash'])
        super().save(*args, **kwargs)


class BillingNotification(models.Model):
    """Immutable in-app facts only; no delivery promise or contact data."""
    TYPES = ('payment_received','cancellation_processing','cancellation_confirmed',
             'credit_refund_reserved','credit_refund_refunded','credit_refund_released',
             'payment_failed','renewal_failed','credit_batch_expired','service_period_ended',
             'topup_authorization_recorded','topup_authorization_paused',
             'topup_revocation_processing','topup_revocation_confirmed',
             'seat_assignment_changed','shared_limit_changed',
             'renewal_payment_received','topup_payment_received','topup_payment_failed')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    type = models.CharField(max_length=32)
    source_event = models.CharField(max_length=160)
    order = models.ForeignKey(Order, null=True, on_delete=models.PROTECT)
    subscription = models.ForeignKey(Subscription, null=True, on_delete=models.PROTECT)
    refund = models.ForeignKey(CreditRefund, null=True, on_delete=models.PROTECT)
    amount_minor = models.PositiveBigIntegerField(null=True)
    currency = models.CharField(max_length=3, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account','source_event'],name='commerce_notification_event_unique')]

    def save(self, *args, **kwargs):
        _unchanged(self, ['account_id','type','source_event','order_id','subscription_id','refund_id',
                         'amount_minor','currency','occurred_at'])
        if self.type not in self.TYPES:
            raise ValueError('INVALID_NOTIFICATION_TYPE')
        super().save(*args, **kwargs)


class TaskCostCompletion(models.Model):
    """Append-only receipt completing an unknown cost, without new usage."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT)
    event = models.OneToOneField(TaskCostEvent, on_delete=models.PROTECT, related_name='cost_completion')
    source = models.CharField(max_length=128)
    request_key = models.CharField(max_length=128)
    cost_amount = models.TextField()
    currency = models.CharField(max_length=3)
    occurred_at = models.DateTimeField()
    evidence_hash = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account','source','request_key'], name='commerce_task_cost_completion_unique')]

    def save(self, *args, **kwargs):
        _unchanged(self, ['account_id','event_id','source','request_key','cost_amount','currency',
                         'occurred_at','evidence_hash','request_hash'])
        super().save(*args, **kwargs)


class CreditEntry(models.Model):
    """Append-only movements, not an independently editable balance."""

    batch = models.ForeignKey(CreditBatch, on_delete=models.PROTECT, related_name="entries")
    reservation = models.ForeignKey(Reservation, null=True, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=12)
    quantity = models.TextField()
    event_key = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "event_key"], name="commerce_credit_event_unique")]
