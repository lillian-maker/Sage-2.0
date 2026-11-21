"""App workbench entities (backend-plan.md section 1, E07–E63)."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from .ids import new_public_id

USER = settings.AUTH_USER_MODEL


def _choices(*values):
    return [(value, value) for value in values]


SHARE_SCOPE = _choices('only_me', 'department', 'account')
VISIBILITY = _choices('only_me', 'workspace', 'account')
EXTENSION_KIND = _choices('plugin', 'skill', 'connector', 'agent')
PUBLISHER_TIER = _choices('official', 'community_reviewed', 'third_party', 'enterprise', 'local_import')
ACCESS_LEVEL = _choices('read_only')
STAGE = _choices('understand', 'research', 'analyze', 'review', 'deliver')
INVITE_STATE = _choices('invited', 'active', 'removed')
MODEL_MODE = _choices('auto', 'standard', 'deep')
REVIEW_DIMENSION = _choices('platform_policy', 'market_law', 'brand', 'facts')
ACTION_KIND = _choices('read_query')


class WorkbenchModel(models.Model):
    """Shared columns for every new workbench table (entity catalog 1.7, Q-D9)."""
    public_id = models.CharField(max_length=26, unique=True, default=new_public_id, editable=False)
    account = models.ForeignKey('backend.BillingAccount', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class GlobalCatalogModel(WorkbenchModel):
    """Catalog rows (system experts, global extensions, template teams) have no tenant."""
    account = models.ForeignKey('backend.BillingAccount', null=True, blank=True,
                                on_delete=models.PROTECT, related_name='+')

    class Meta:
        abstract = True


# E07
class AccountPolicy(WorkbenchModel):
    approval_required_actions = models.JSONField(default=list)
    approver_rule = models.CharField(max_length=24, blank=True, default='')
    expert_share_max_scope = models.CharField(max_length=12, choices=SHARE_SCOPE)
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    byok_enabled = models.BooleanField(default=False)
    extension_allowlist_kinds = models.JSONField(default=list)
    extension_allowed_tiers = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account'], name='wb_policy_account_unique')]


# E08
class ExtensionAllowEntry(WorkbenchModel):
    extension = models.ForeignKey('Extension', on_delete=models.PROTECT, related_name='+')
    added_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'extension'], name='wb_allow_entry_unique')]


# E11
class NotificationPreference(WorkbenchModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    type = models.CharField(max_length=20, choices=_choices('needs_action', 'task_completed', 'mention_assign', 'billing'))
    channel = models.CharField(max_length=8, choices=_choices('in_app', 'push', 'email'))
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'user', 'type', 'channel'],
                                               name='wb_notification_pref_unique')]


# E13
class ShortcutBinding(WorkbenchModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    action = models.CharField(max_length=32, choices=_choices(
        'send_task', 'newline', 'toggle_sidebar', 'select_expert', 'open_settings', 'cite_document',
        'show_shortcuts', 'select_skill', 'toggle_orchestration', 'hand_to_expert', 'fork_from_message',
        'ignore', 'close_overlay'))
    keys = models.CharField(max_length=32)


# E14
class Workspace(WorkbenchModel):
    name = models.CharField(max_length=40)
    description = models.CharField(max_length=200, null=True, blank=True)
    current_rule_version = models.ForeignKey('WorkspaceRuleVersion', null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name='+')
    created_from = models.CharField(max_length=12, choices=_choices('blank', 'copy_rules', 'playbook'))
    source_workspace = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_playbook = models.ForeignKey('Playbook', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [models.Index(fields=['account', 'deleted_at', 'name'], name='wb_workspace_list')]


# E15
class WorkspaceMember(WorkbenchModel):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    invite_email = models.EmailField(null=True, blank=True)
    role = models.CharField(max_length=8, choices=_choices('owner', 'editor', 'viewer', 'guest'))
    state = models.CharField(max_length=10, choices=INVITE_STATE)
    guest_tasks = models.ManyToManyField('Task', blank=True, related_name='+')
    invited_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    accepted_at = models.DateTimeField(null=True, blank=True)
    pinned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'user'], condition=models.Q(user__isnull=False),
                                    name='wb_workspace_member_user_unique'),
            models.UniqueConstraint(fields=['workspace', 'invite_email'], condition=models.Q(state='invited'),
                                    name='wb_workspace_member_invite_unique'),
        ]


# E16
class WorkspaceRuleVersion(WorkbenchModel):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='rule_versions')
    number = models.PositiveIntegerField()
    rules = models.JSONField(default=list)
    author = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    change_note = models.CharField(max_length=200)
    restored_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'number'], name='wb_rule_version_unique')]


# E17 (replan_count is derived from TaskStep.plan_revision, C-F-16)
class Task(WorkbenchModel):
    STATUSES = ('draft', 'awaiting_kickoff', 'running', 'awaiting_user', 'paused', 'stuck', 'delivered',
                'needs_revision', 'accepted', 'stopped')
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.PROTECT, related_name='tasks')
    title = models.CharField(max_length=80)
    creator = models.ForeignKey('backend.Membership', on_delete=models.PROTECT, related_name='+')
    owner = models.ForeignKey('backend.Membership', on_delete=models.PROTECT, related_name='+')
    lead_expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    lead_expert_auto = models.BooleanField(default=False)
    team = models.ForeignKey('ExpertTeam', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    model_mode = models.CharField(max_length=16, choices=MODEL_MODE)
    status = models.CharField(max_length=20, default='draft', choices=_choices(*STATUSES))
    complexity = models.CharField(max_length=8, null=True, blank=True, choices=_choices('simple', 'normal', 'complex'))
    estimated_expert_count = models.PositiveSmallIntegerField(null=True, blank=True)
    estimated_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    goal = models.TextField(null=True, blank=True)
    scope_text = models.TextField(null=True, blank=True)
    deliverables = models.TextField(null=True, blank=True)
    constraints = models.TextField(null=True, blank=True)
    rule_version = models.ForeignKey(WorkspaceRuleVersion, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    forked_from_task = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    forked_from_message = models.ForeignKey('TaskMessage', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_inbox_item = models.ForeignKey('InboxItem', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_automation_run = models.ForeignKey('AutomationRun', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_playbook = models.ForeignKey('Playbook', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    playbook_variables = models.JSONField(null=True, blank=True)
    shared_task = models.ForeignKey('commerce.SharedTask', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    pause_reason = models.CharField(max_length=20, null=True, blank=True,
                                    choices=_choices('user', 'quota_exhausted', 'handoff', 'policy'))
    stuck_rounds = models.PositiveSmallIntegerField(default=0)
    archived_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['account', 'owner', 'archived_at', 'last_activity_at'], name='wb_task_owner_list'),
            models.Index(fields=['account', 'workspace', 'last_activity_at'], name='wb_task_workspace_list'),
            models.Index(fields=['account', 'status'], name='wb_task_status'),
        ]


# E18 (role max_length widened from 12: "shared_viewer"/"shared_editor" are 13 characters)
class TaskParticipant(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='participants')
    kind = models.CharField(max_length=8, choices=_choices('human', 'expert'))
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=16, choices=_choices('lead', 'collaborator', 'shared_viewer', 'shared_editor'))
    state = models.CharField(max_length=10, choices=INVITE_STATE)
    invited_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['task', 'user'], name='wb_task_participant_user_unique'),
                       models.UniqueConstraint(fields=['task', 'expert'], name='wb_task_participant_expert_unique')]


# E19
class TaskUserState(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='user_states')
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    last_read_at = models.DateTimeField(null=True, blank=True)
    marked_unread = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['task', 'user'], name='wb_task_user_state_unique')]


# E20
class TaskThread(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='threads')
    kind = models.CharField(max_length=8, choices=_choices('main', 'side'))
    anchor_message = models.ForeignKey('TaskMessage', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    brought_back_message = models.ForeignKey('TaskMessage', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['task'], condition=models.Q(kind='main'),
                                               name='wb_task_one_main_thread')]


# E21 (kind max_length widened from 16: "authorization_card" is 18 characters)
class TaskMessage(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='messages')
    thread = models.ForeignKey(TaskThread, on_delete=models.CASCADE, related_name='messages')
    author_kind = models.CharField(max_length=8, choices=_choices('user', 'expert', 'system'))
    author_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    author_expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=20, choices=_choices(
        'text', 'kickoff_card', 'plan', 'authorization_card', 'parallel_card', 'summary', 'suggestion',
        'delivery_card', 'review_card', 'quota_notice', 'system'))
    body = models.TextField(null=True, blank=True)
    card_type = models.CharField(max_length=24, null=True, blank=True)
    card_id = models.CharField(max_length=26, null=True, blank=True)
    parent_message = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    branch_index = models.PositiveSmallIntegerField(default=1)
    is_important = models.BooleanField(default=False)
    sent_at = models.DateTimeField(default=timezone.now)
    attached_documents = models.ManyToManyField('KnowledgeDocument', blank=True, related_name='+')
    run_metrics = models.JSONField(null=True, blank=True)
    mentioned_experts = models.ManyToManyField('Expert', blank=True, related_name='+')
    requested_skills = models.ManyToManyField('Extension', blank=True, related_name='+')

    class Meta:
        indexes = [models.Index(fields=['task', 'thread', 'sent_at'], name='wb_message_thread_order')]


# E22
class KickoffQuestion(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='kickoff_questions')
    order = models.PositiveSmallIntegerField()
    question = models.CharField(max_length=200)
    options = models.JSONField(null=True, blank=True)
    answer = models.CharField(max_length=500, null=True, blank=True)
    answered_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    answered_at = models.DateTimeField(null=True, blank=True)


# E23
class AcceptanceCriterion(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='criteria')
    step = models.ForeignKey('TaskStep', null=True, blank=True, on_delete=models.CASCADE, related_name='criteria')
    order = models.PositiveSmallIntegerField()
    text = models.CharField(max_length=200)
    origin = models.CharField(max_length=8, choices=_choices('ai', 'user'))
    verdict = models.CharField(max_length=8, default='pending', choices=_choices('pending', 'met', 'unmet'))
    evidence = models.CharField(max_length=120, null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)


# E24
class TaskStep(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='steps')
    order = models.PositiveSmallIntegerField()
    stage = models.CharField(max_length=12, choices=STAGE)
    title = models.CharField(max_length=80)
    # Short capability name on the orchestration node header (“销量估算”); empty falls back to skill, then expert.
    label = models.CharField(max_length=40, blank=True, default='')
    expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    skill = models.ForeignKey('Extension', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    required_extension = models.ForeignKey('Extension', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=20, default='queued', choices=_choices(
        'queued', 'running', 'awaiting_user', 'done', 'failed', 'missing_capability', 'skipped'))
    estimated_credits = models.PositiveIntegerField(null=True, blank=True)
    in_background = models.BooleanField(default=False)
    activity = models.CharField(max_length=120, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    inputs = models.JSONField(null=True, blank=True)
    depends_on = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='dependents')
    plan_revision = models.PositiveSmallIntegerField(default=1)
    assist_experts = models.ManyToManyField('Expert', blank=True, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['task', 'plan_revision', 'order'], name='wb_task_step_order_unique')]


# E25
class AuthorizationRequest(WorkbenchModel):
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE, related_name='authorization_requests')
    automation_run = models.ForeignKey('AutomationRun', null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    step = models.ForeignKey(TaskStep, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    requested_by_expert = models.ForeignKey('Expert', on_delete=models.PROTECT, related_name='+')
    extension = models.ForeignKey('Extension', on_delete=models.PROTECT, related_name='+')
    action_kind = models.CharField(max_length=16, choices=ACTION_KIND)
    scope_text = models.CharField(max_length=200)
    scope_params = models.JSONField(default=dict)
    estimate_state = models.CharField(max_length=12, default='pending',
                                      choices=_choices('pending', 'checking', 'estimated', 'unavailable'))
    estimated_credits = models.PositiveIntegerField(null=True, blank=True)
    validity = models.CharField(max_length=12, choices=_choices('this_request', 'this_task'))
    decision = models.CharField(max_length=10, default='pending', choices=_choices('pending', 'approved', 'rejected', 'expired'))
    decision_scope = models.CharField(max_length=10, null=True, blank=True, choices=_choices('once', 'task', 'workspace'))
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    reservation = models.ForeignKey('commerce.Reservation', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    policy_approval_required = models.BooleanField(default=False)
    client_request_id = models.CharField(max_length=64, null=True, blank=True)
    purpose_text = models.CharField(max_length=300, blank=True, default='')
    # Short headline for inbox rows (“使用 Jungle Scout 读取竞品销量”); empty falls back to extension + action.
    title = models.CharField(max_length=80, blank=True, default='')
    scope_options = models.JSONField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'client_request_id'], name='wb_authorization_request_idem')]


# E26
class AuthorizationGrant(WorkbenchModel):
    user = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    scope = models.CharField(max_length=10, choices=_choices('task', 'workspace'))
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    extension = models.ForeignKey('Extension', on_delete=models.PROTECT, related_name='+')
    action_kind = models.CharField(max_length=16, choices=ACTION_KIND)
    source_request = models.ForeignKey(AuthorizationRequest, on_delete=models.PROTECT, related_name='grants')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['user', 'extension', 'action_kind', 'scope', 'task', 'workspace'],
            condition=models.Q(revoked_at__isnull=True), name='wb_authorization_grant_active_unique')]


# E27
class TaskDelivery(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='deliveries')
    artifact_version = models.ForeignKey('ArtifactVersion', on_delete=models.PROTECT, related_name='+')
    summary = models.TextField()
    decision = models.CharField(max_length=10, default='pending', choices=_choices('pending', 'accepted', 'returned'))
    return_reason = models.TextField(null=True, blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)


# E28
class TaskSuggestion(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='suggestions')
    text = models.CharField(max_length=200)
    requires_authorization = models.BooleanField(default=False)
    state = models.CharField(max_length=10, default='open', choices=_choices('open', 'started', 'dismissed'))
    created_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')


# E29 (artifact_version replaced by artifact_versions, C-F-8)
class ContentReview(WorkbenchModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='reviews')
    reviewers = models.ManyToManyField('Expert', related_name='+')
    dimensions = models.JSONField(default=list)
    conclusion = models.CharField(max_length=200)
    state = models.CharField(max_length=12, choices=_choices('reviewed', 'rewriting', 'rewritten', 'verified'))
    result_version = models.ForeignKey('ArtifactVersion', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    artifact_versions = models.ManyToManyField('ArtifactVersion', related_name='+')
    target_platform = models.CharField(max_length=40, blank=True, default='')
    target_market = models.CharField(max_length=40, blank=True, default='')
    verifier_expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    client_request_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'client_request_id'], name='wb_content_review_idem')]


class ContentReviewIssue(WorkbenchModel):
    review = models.ForeignKey(ContentReview, on_delete=models.CASCADE, related_name='issues')
    dimension = models.CharField(max_length=16, choices=REVIEW_DIMENSION)
    severity = models.CharField(max_length=8, choices=_choices('high', 'medium', 'low'))
    # Required only for text issues (C-F-19); image issues use suggested_artifact_version.
    original_text = models.TextField(blank=True, default='')
    suggested_text = models.TextField(blank=True, default='')
    basis = models.CharField(max_length=300)
    action = models.CharField(max_length=10, default='pending', choices=_choices('pending', 'applied', 'ignored'))
    location = models.CharField(max_length=60, null=True, blank=True)
    highlight_spans = models.JSONField(null=True, blank=True)
    basis_rule_version = models.ForeignKey(WorkspaceRuleVersion, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    basis_rule_index = models.PositiveSmallIntegerField(null=True, blank=True)
    title = models.CharField(max_length=120, blank=True, default='')
    suggested_artifact_version = models.ForeignKey('ArtifactVersion', null=True, blank=True,
                                                   on_delete=models.PROTECT, related_name='+')


# E30
class Artifact(WorkbenchModel):
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.PROTECT, related_name='artifacts')
    source_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='artifacts')
    source_automation = models.ForeignKey('Automation', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    title = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=_choices('document', 'spreadsheet', 'presentation', 'webpage', 'image'))
    current_version = models.ForeignKey('ArtifactVersion', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_by_expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    trashed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['account', 'workspace', 'trashed_at', 'updated_at'], name='wb_artifact_list')]


# E31
class ArtifactVersion(WorkbenchModel):
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name='versions')
    number = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=10, default='draft', choices=_choices('draft', 'accepted', 'published'))
    label = models.CharField(max_length=20, blank=True, default='')
    author_expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    author_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    change_summary = models.CharField(max_length=200, null=True, blank=True)
    restored_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    task_message = models.ForeignKey(TaskMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    content_ref = models.CharField(max_length=200)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    client_request_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['artifact', 'number'], name='wb_artifact_version_unique'),
                       models.UniqueConstraint(fields=['account', 'client_request_id'], name='wb_artifact_version_idem')]


# E32 (audience max_length widened from 16: "workspace_members" is 17 characters)
class ArtifactShare(WorkbenchModel):
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name='shares')
    audience = models.CharField(max_length=20, choices=_choices('only_me', 'workspace_members', 'public_link', 'encrypted_link'))
    token = models.CharField(max_length=43, null=True, blank=True, unique=True)
    secret_hash = models.CharField(max_length=128, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)


# E33
class Playbook(WorkbenchModel):
    name = models.CharField(max_length=40)
    source_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    variables = models.JSONField(default=list)
    visibility = models.CharField(max_length=12, choices=VISIBILITY)
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')


# E34
class PlaybookStep(WorkbenchModel):
    playbook = models.ForeignKey(Playbook, on_delete=models.CASCADE, related_name='steps')
    order = models.PositiveSmallIntegerField()
    stage = models.CharField(max_length=12, choices=STAGE)
    title = models.CharField(max_length=80)
    expert = models.ForeignKey('Expert', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    skill = models.ForeignKey('Extension', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    depends_on = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='dependents')
    acceptance = models.JSONField(default=list, blank=True)


# E35
class Expert(GlobalCatalogModel):
    source = models.CharField(max_length=8, choices=_choices('system', 'custom'))
    group = models.CharField(max_length=12, choices=_choices('management', 'role'))
    catalog_code = models.CharField(max_length=16, null=True, blank=True, unique=True)
    name = models.CharField(max_length=16)
    title = models.CharField(max_length=40)
    domain = models.CharField(max_length=24, blank=True, default='')
    specialty = models.CharField(max_length=40, null=True, blank=True)
    responsibilities = models.TextField()
    capability_tags = models.JSONField(default=list, blank=True)
    avatar_ref = models.CharField(max_length=200, null=True, blank=True)
    version_label = models.CharField(max_length=16)
    status = models.CharField(max_length=10, default='draft', choices=_choices('draft', 'published', 'archived'))
    share_scope = models.CharField(max_length=12, choices=SHARE_SCOPE)
    shared_department = models.ForeignKey('commerce.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    template_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    start_mode = models.CharField(max_length=10, null=True, blank=True, choices=_choices('describe', 'template', 'blank'))
    start_description = models.CharField(max_length=500, null=True, blank=True)
    communication_style = models.CharField(max_length=16, choices=_choices('concise', 'detailed', 'conclusion_first'))
    uncertainty_policy = models.CharField(max_length=16, choices=_choices('ask_first', 'act_then_confirm'))
    forbidden_rules = models.JSONField(default=list, blank=True)
    knowledge_bases = models.ManyToManyField('KnowledgeBase', blank=True, related_name='+')
    wizard_step = models.PositiveSmallIntegerField(null=True, blank=True)
    reports_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    collaboration_note = models.CharField(max_length=60, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'name'], condition=~models.Q(status='archived'),
                                               name='wb_expert_account_name_unique')]


# E36
class ExpertCapability(WorkbenchModel):
    expert = models.ForeignKey(Expert, on_delete=models.CASCADE, related_name='capabilities')
    extension = models.ForeignKey('Extension', on_delete=models.PROTECT, related_name='+')
    access = models.CharField(max_length=10, default='read_only', choices=ACCESS_LEVEL)
    required_for = models.ForeignKey('Extension', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expert', 'extension'], name='wb_expert_capability_unique')]


# E37 (account null for template teams, C-E-8 / Q-E-6 A)
class ExpertTeam(GlobalCatalogModel):
    name = models.CharField(max_length=40)
    description = models.CharField(max_length=200, null=True, blank=True)
    members = models.ManyToManyField(Expert, related_name='+')
    lead_expert = models.ForeignKey(Expert, on_delete=models.PROTECT, related_name='+')
    source = models.CharField(max_length=10, choices=_choices('template', 'custom'))
    visibility = models.CharField(max_length=12, choices=_choices('only_me', 'account'))
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')


# E38
class Extension(GlobalCatalogModel):
    kind = models.CharField(max_length=10, choices=EXTENSION_KIND)
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200)
    publisher_tier = models.CharField(max_length=20, choices=PUBLISHER_TIER)
    latest_version = models.CharField(max_length=16)
    access = models.CharField(max_length=10, default='read_only', choices=ACCESS_LEVEL)
    permissions = models.JSONField(default=list)
    required_connector = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    prerequisite_inputs = models.JSONField(default=list, blank=True)
    outputs = models.JSONField(default=list, blank=True)
    acceptance_template = models.JSONField(default=list, blank=True)
    billing_note = models.CharField(max_length=60, null=True, blank=True)
    global_usage_count = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'kind', 'name'], name='wb_extension_name_unique')]


# E39
class ExtensionInstall(WorkbenchModel):
    extension = models.ForeignKey(Extension, on_delete=models.PROTECT, related_name='installs')
    installed_version = models.CharField(max_length=16)
    enabled = models.BooleanField(default=True)
    installed_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    pending_permission_review = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'extension'], name='wb_extension_install_unique')]


# E40
class ConnectorConnection(WorkbenchModel):
    extension = models.ForeignKey(Extension, on_delete=models.PROTECT, related_name='connections')
    owner_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    state = models.CharField(max_length=14, default='not_connected', choices=_choices(
        'not_connected', 'connected', 'expired', 'disabled', 'revoked'))
    credential_ref = models.CharField(max_length=128, null=True, blank=True)
    connected_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    connected_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=40, null=True, blank=True)
    revoked_reason = models.CharField(max_length=20, null=True, blank=True, choices=_choices('user', 'offboarding', 'admin'))


# E41
class KnowledgeBase(WorkbenchModel):
    name = models.CharField(max_length=60)
    scope = models.CharField(max_length=10, choices=_choices('personal', 'workspace', 'account'))
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.PROTECT, related_name='knowledge_bases')
    owner = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    cite_audience = models.CharField(max_length=16, choices=_choices('owner_only', 'workspace_all', 'account_all'))
    editors = models.ManyToManyField(USER, blank=True, related_name='+')
    editor_department = models.ForeignKey('commerce.Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    expert_cite_enabled = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)


# E42
class KnowledgeDocument(WorkbenchModel):
    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.PROTECT, related_name='documents')
    title = models.CharField(max_length=200)
    source_kind = models.CharField(max_length=14, choices=_choices('upload', 'expert_output', 'web'))
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    source_artifact_version = models.ForeignKey(ArtifactVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_url = models.URLField(null=True, blank=True)
    refresh_interval = models.CharField(max_length=10, null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    index_state = models.CharField(max_length=10, default='queued', choices=_choices('queued', 'indexing', 'indexed', 'failed'))
    index_progress = models.PositiveSmallIntegerField(null=True, blank=True)
    data_date = models.DateField(null=True, blank=True)
    content_ref = models.CharField(max_length=200)
    deleted_at = models.DateTimeField(null=True, blank=True)


# E43
class Citation(WorkbenchModel):
    document = models.ForeignKey(KnowledgeDocument, null=True, blank=True, on_delete=models.PROTECT, related_name='citations')
    fact = models.ForeignKey('KnowledgeFact', null=True, blank=True, on_delete=models.PROTECT, related_name='citations')
    external_label = models.CharField(max_length=200, null=True, blank=True)
    artifact_version = models.ForeignKey(ArtifactVersion, null=True, blank=True, on_delete=models.CASCADE, related_name='citations')
    task_message = models.ForeignKey(TaskMessage, null=True, blank=True, on_delete=models.CASCADE, related_name='citations')
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='citations')
    ref_index = models.PositiveSmallIntegerField(null=True, blank=True)
    locator = models.CharField(max_length=40, null=True, blank=True)
    cited_version = models.PositiveSmallIntegerField(null=True, blank=True)
    task_step = models.ForeignKey(TaskStep, null=True, blank=True, on_delete=models.SET_NULL, related_name='citations')

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(document__isnull=False) | models.Q(fact__isnull=False) | models.Q(external_label__isnull=False),
            name='wb_citation_has_source')]
        indexes = [models.Index(fields=['document', 'created_at'], name='wb_citation_document')]


# E44
class KnowledgeEntity(WorkbenchModel):
    name = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=12, choices=_choices(
        'market', 'competitor', 'regulation', 'product', 'channel', 'task', 'time'))
    documents = models.ManyToManyField(KnowledgeDocument, blank=True, related_name='+')
    subtitle = models.CharField(max_length=80, null=True, blank=True)


# E45
class KnowledgeRelation(WorkbenchModel):
    from_entity = models.ForeignKey(KnowledgeEntity, on_delete=models.CASCADE, related_name='outgoing_relations')
    to_entity = models.ForeignKey(KnowledgeEntity, on_delete=models.CASCADE, related_name='incoming_relations')
    kind = models.CharField(max_length=16, choices=_choices(
        'belongs_to', 'requires', 'plans_to_enter', 'cites', 'top_competitor', 'channel'))

    class Meta:
        constraints = [models.UniqueConstraint(fields=['from_entity', 'to_entity', 'kind'], name='wb_knowledge_relation_unique')]


# E46
class KnowledgeFact(WorkbenchModel):
    entity = models.ForeignKey(KnowledgeEntity, on_delete=models.CASCADE, related_name='facts')
    statement = models.CharField(max_length=300)
    source_document = models.ForeignKey(KnowledgeDocument, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_date = models.DateField(null=True, blank=True)
    validity = models.CharField(max_length=10, default='current', choices=_choices('current', 'superseded'))
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    conflict_key = models.CharField(max_length=64, null=True, blank=True)


# E47 (responsible_expert replaced by responsible_experts, C-B-2; kind and sender_filter widened
# from 20: "amazon_buyer_messages" is 21 and "all_members_except_bots" is 23 characters)
class InboxSource(WorkbenchModel):
    owner_user = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=24, choices=_choices(
        'company_email', 'amazon_buyer_messages', 'store_notifications', 'feishu', 'wecom'))
    connection = models.ForeignKey(ConnectorConnection, on_delete=models.PROTECT, related_name='inbox_sources')
    watched_groups = models.JSONField(default=list, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    sender_filter = models.CharField(max_length=24)
    initiative = models.CharField(max_length=16, default='report_only',
                                  choices=_choices('report_only', 'draft_suggestion', 'start_task'))
    disconnected_at = models.DateTimeField(null=True, blank=True)
    responsible_experts = models.ManyToManyField(Expert, blank=True, related_name='+')
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner_user', 'kind', 'connection'], name='wb_inbox_source_unique')]


# E48
class InboxSourceDailyStat(WorkbenchModel):
    source = models.ForeignKey(InboxSource, on_delete=models.CASCADE, related_name='daily_stats')
    date = models.DateField()
    received = models.PositiveIntegerField(default=0)
    after_rules = models.PositiveIntegerField(default=0)
    after_expert = models.PositiveIntegerField(default=0)
    admitted = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'date'], name='wb_inbox_stat_unique')]


# E49
class InboxItem(WorkbenchModel):
    recipient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    source = models.ForeignKey(InboxSource, on_delete=models.PROTECT, related_name='items')
    external_ref = models.CharField(max_length=128)
    title = models.CharField(max_length=120)
    received_at = models.DateTimeField()
    original_text = models.TextField()
    translated_text = models.TextField(null=True, blank=True)
    group = models.CharField(max_length=20, choices=_choices('pending_confirmation', 'todo', 'in_progress', 'done', 'ignored'))
    confidence = models.PositiveSmallIntegerField()
    priority = models.CharField(max_length=8, default='normal', choices=_choices('high', 'normal'))
    rationale = models.TextField()
    suggestion = models.TextField(null=True, blank=True)
    suggested_experts = models.ManyToManyField(Expert, blank=True, related_name='+')
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    ignored_at = models.DateTimeField(null=True, blank=True)
    sender_label = models.CharField(max_length=60, blank=True, default='')
    # Channel shown on the inbox card ("Amazon.de", "供应商邮件"); sender_label stays the list-view sender.
    channel_label = models.CharField(max_length=40, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'external_ref'], name='wb_inbox_item_unique')]
        indexes = [models.Index(fields=['recipient', 'group', 'received_at'], name='wb_inbox_item_list')]


# E50
class InboxItemEvent(WorkbenchModel):
    item = models.ForeignKey(InboxItem, on_delete=models.CASCADE, related_name='events')
    at = models.DateTimeField()
    kind = models.CharField(max_length=16)
    text = models.CharField(max_length=200)


# E51
class HandoffItem(WorkbenchModel):
    removal = models.ForeignKey('backend.MemberRemoval', on_delete=models.PROTECT, related_name='+')
    successor = models.ForeignKey('backend.Membership', on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=20, choices=_choices(
        'task', 'approval', 'automation', 'workspace_ownership', 'custom_expert'))
    target_type = models.CharField(max_length=40)
    target_id = models.CharField(max_length=64)
    # 离职交接把 running/awaiting_user 的任务暂停时，接手人确认后要恢复到的状态
    resume_status = models.CharField(max_length=16, null=True, blank=True)
    state = models.CharField(max_length=12, default='pending', choices=_choices('pending', 'accepted', 'reactivated', 'dismissed'))
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['removal', 'target_type', 'target_id'], name='wb_handoff_item_unique')]


# E52
class Automation(WorkbenchModel):
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.PROTECT, related_name='automations')
    owner = models.ForeignKey('backend.Membership', on_delete=models.PROTECT, related_name='+')
    name = models.CharField(max_length=60)
    lead_expert = models.ForeignKey(Expert, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    content_kind = models.CharField(max_length=10, choices=_choices('prompt', 'playbook'))
    prompt = models.TextField(null=True, blank=True)
    playbook = models.ForeignKey(Playbook, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    playbook_variables = models.JSONField(null=True, blank=True)
    schedule_kind = models.CharField(max_length=8, choices=_choices('daily', 'weekly', 'monthly'))
    schedule_time = models.TimeField()
    schedule_weekday = models.PositiveSmallIntegerField(null=True, blank=True)
    schedule_monthday = models.PositiveSmallIntegerField(null=True, blank=True)
    timezone = models.CharField(max_length=40)
    state = models.CharField(max_length=10, default='draft', choices=_choices('draft', 'enabled', 'paused'))
    next_run_at = models.DateTimeField(null=True, blank=True)
    estimated_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    estimated_credits = models.PositiveIntegerField(null=True, blank=True)
    expected_output = models.CharField(max_length=120, null=True, blank=True)
    created_from_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    version = models.PositiveIntegerField(default=1)


# E53
class AutomationPermission(WorkbenchModel):
    automation = models.ForeignKey(Automation, on_delete=models.CASCADE, related_name='permissions')
    kind = models.CharField(max_length=16, choices=_choices('connector_read', 'workspace_write'))
    extension = models.ForeignKey(Extension, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    state = models.CharField(max_length=18, choices=_choices('needs_connection', 'needs_grant', 'granted'))
    grant = models.ForeignKey(AuthorizationGrant, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['automation', 'kind', 'extension'], name='wb_automation_permission_unique')]


# E54
class AutomationRun(WorkbenchModel):
    automation = models.ForeignKey(Automation, on_delete=models.PROTECT, related_name='runs')
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    title = models.CharField(max_length=120)
    trigger = models.CharField(max_length=10, choices=_choices('schedule', 'manual'))
    scheduled_for = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=14, default='scheduled', choices=_choices(
        'scheduled', 'running', 'awaiting_user', 'succeeded', 'failed', 'missed'))
    failure_code = models.CharField(max_length=40, null=True, blank=True)
    missed_reason = models.CharField(max_length=40, null=True, blank=True, choices=_choices('mac_asleep', 'app_not_running'))
    variables = models.JSONField(null=True, blank=True)
    client_request_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['automation', 'scheduled_for'], condition=models.Q(trigger='schedule'),
                                               name='wb_automation_run_schedule_unique'),
                       models.UniqueConstraint(fields=['account', 'client_request_id'], name='wb_automation_run_idem')]


# E55
class MemoryItem(WorkbenchModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    category = models.CharField(max_length=16, choices=_choices('about_me', 'recent_projects', 'lessons'))
    content = models.CharField(max_length=300)
    source_kind = models.CharField(max_length=12, choices=_choices('onboarding', 'task', 'chat_command'))
    source_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    edited_at = models.DateTimeField(null=True, blank=True)


# E56
class AppNotification(WorkbenchModel):
    recipient = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    category = models.CharField(max_length=16, choices=_choices('task_progress', 'automation', 'mention', 'workspace'))
    type = models.CharField(max_length=32, choices=_choices(
        'step_completed', 'delivered', 'automation_missed', 'mentioned', 'assigned', 'quota_reminder_to_admin',
        'join_requested', 'join_approved', 'join_rejected'))
    text = models.CharField(max_length=200)
    context_label = models.CharField(max_length=80, blank=True, default='')
    target_type = models.CharField(max_length=40, blank=True, default='')
    target_id = models.CharField(max_length=64, blank=True, default='')
    action = models.CharField(max_length=16, null=True, blank=True, choices=_choices('run_now', 'open', 'go_inbox'))
    occurred_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=64)
    actor_expert = models.ForeignKey(Expert, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['recipient', 'dedupe_key'], name='wb_app_notification_dedupe')]


# E63
class ArtifactUserState(WorkbenchModel):
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name='user_states')
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    last_opened_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['artifact', 'user'], name='wb_artifact_user_state_unique')]

# D33 (83 屏「你还不是这个工作区的成员 · 申请加入」)
class WorkspaceJoinRequest(WorkbenchModel):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='join_requests')
    requester = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    message = models.CharField(max_length=200, blank=True, default='')
    state = models.CharField(max_length=10, default='pending',
                             choices=_choices('pending', 'approved', 'rejected', 'withdrawn'))
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_role = models.CharField(max_length=8, null=True, blank=True, choices=_choices('editor', 'viewer'))
    reject_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'requester'], condition=models.Q(state='pending'),
                                               name='wb_join_request_pending_unique')]
        indexes = [models.Index(fields=['workspace', 'state', 'created_at'], name='wb_join_request_list')]


# D31 (65 屏 加购跳转官网“自动带上当前账本”): one-time website sign-in, bound to user + ledger (account).
class BillingHandoff(WorkbenchModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    purpose = models.CharField(max_length=16, choices=_choices('topup'))
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)


# D41 (75/78 屏): commerce invitations and member rows carry no display name / user id; keyed by their ids.
class OrgPersonProfile(WorkbenchModel):
    invitation_id = models.UUIDField(null=True, blank=True, unique=True)
    membership = models.OneToOneField('backend.Membership', null=True, blank=True, on_delete=models.CASCADE,
                                      related_name='+')
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    display_name = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(invitation_id__isnull=False) | models.Q(membership__isnull=False),
            name='wb_org_person_profile_target')]
