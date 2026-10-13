"""Sales-only admin: no billing or authentication models are exposed here."""
import csv

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.main import IncorrectLookupParameters
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .consultations import purge_record, sales_access, update_sales
from .models import Consultation, ConsultationAudit
from .models import AccountDeletionRequest
from .models import RegistrationAttribution
from .account import update_deletion_request

# A sales console must not turn into an identity/permissions editor.
for model in (get_user_model(), Group):
    if admin.site.is_registered(model):
        admin.site.unregister(model)


class ConsultationForm(forms.ModelForm):
    edit_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Consultation
        fields = ('owner', 'status', 'notes')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['edit_version'].initial = self.instance.version
        users = get_user_model().objects.filter(is_active=True, is_staff=True)
        self.fields['owner'].queryset = users.filter(pk__in=[user.pk for user in users
                                                           if sales_access(user)])
        self.fields['notes'].max_length = 4000


class AccountDeletionForm(forms.ModelForm):
    class Meta:
        model = AccountDeletionRequest
        fields = ('status', 'progress_message', 'delay_reason', 'expected_completion_at')


@admin.register(AccountDeletionRequest)
class AccountDeletionAdmin(admin.ModelAdmin):
    form = AccountDeletionForm
    list_display = ('id', 'user_id', 'status', 'created_at', 'first_response_due_at', 'completion_due_at')
    readonly_fields = ('id', 'user', 'policy_version', 'blockers', 'created_at', 'first_response_due_at',
        'completion_due_at', 'first_responded_at', 'completed_at', 'retain_until')
    fields = readonly_fields + ('status', 'progress_message', 'delay_reason', 'expected_completion_at')
    actions = None

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff and request.user.has_perm('backend.manage_account_deletion')

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or obj.status != 'completed')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        update_deletion_request(obj.pk, actor=request.user, status=obj.status,
            progress_message=obj.progress_message, delay_reason=obj.delay_reason,
            expected_completion_at=obj.expected_completion_at)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(request.path)

    def log_change(self, request, obj, message):
        # Dedicated audit stores action keys, never reviewer free text.
        pass


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    form = ConsultationForm
    list_display = ('id', 'company', 'status', 'owner', 'created_at', 'completed_at', 'purged_at')
    list_filter = ('status', 'purged_at')
    list_per_page = 30
    ordering = ('-created_at',)
    readonly_fields = ('id', 'company', 'phone', 'email', 'question', 'created_at',
                       'updated_at', 'completed_at', 'purge_at', 'purged_at')
    fields = readonly_fields + ('owner', 'status', 'notes', 'edit_version')
    actions = ('purge_personal_information',)

    def has_module_permission(self, request):
        return sales_access(request.user)

    def has_view_permission(self, request, obj=None):
        return sales_access(request.user)

    def has_change_permission(self, request, obj=None):
        return sales_access(request.user) and (obj is None or not obj.purged_at)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        update_sales(obj.pk, actor=request.user, version=form.cleaned_data['edit_version'],
            owner_id=obj.owner_id, status=obj.status, notes=obj.notes,
            request_id=getattr(request, 'request_id', None))

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        try:
            with transaction.atomic():
                return super().changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(request.path)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not sales_access(request.user, 'purge_consultation'):
            actions.pop('purge_personal_information', None)
        return actions

    @admin.action(description='提前清除所选申请的个人信息（需要确认）')
    def purge_personal_information(self, request, queryset):
        if not sales_access(request.user, 'purge_consultation'):
            return HttpResponse(status=403)
        if request.POST.get('confirm_purge') != 'yes':
            selected = format_html_join('', '<input type="hidden" name="_selected_action" value="{}">',
                                       ((str(item.pk),) for item in queryset))
            return HttpResponse(format_html(
                '<!doctype html><html lang="zh"><meta charset="utf-8"><title>确认清除个人信息</title>'
                '<h1>确认清除个人信息</h1><p>将清除所选 {} 项申请的公司、联系方式、问题全文和备注。'
                '操作不可恢复，处理历史仅保留无原文审计。</p><form method="post">'
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">{}'
                '<input type="hidden" name="action" value="purge_personal_information">'
                '<button name="confirm_purge" value="yes">确认清除</button></form></html>',
                queryset.count(), get_token(request), selected))
        with transaction.atomic():
            count = sum(purge_record(pk, actor=request.user) for pk in queryset.values_list('pk', flat=True))
        self.message_user(request, f'已清除 {count} 项申请的个人信息。')

    def log_change(self, request, obj, message):
        # Dedicated audit above intentionally excludes free-text/admin representations.
        pass


@admin.register(ConsultationAudit)
class ConsultationAuditAdmin(admin.ModelAdmin):
    list_display = ('consultation_id', 'actor_id', 'action', 'created_at')
    readonly_fields = ('consultation', 'actor', 'action', 'request_id', 'created_at')

    def has_module_permission(self, request):
        return sales_access(request.user)

    def has_view_permission(self, request, obj=None):
        return sales_access(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegistrationAttribution)
class RegistrationAttributionAdmin(admin.ModelAdmin):
    list_display = ('user_number', 'invitation_code_text', 'blogger', 'registration_time')
    readonly_fields = list_display
    fields = list_display
    list_filter = ('invitation_code__code', 'invitation_code__blogger', ('registered_at', admin.DateFieldListFilter))
    list_per_page = 30
    ordering = ('-registered_at', '-pk')
    actions = None
    change_list_template = 'admin/backend/registrationattribution/change_list.html'

    @admin.display(description='用户编号')
    def user_number(self, obj):
        return obj.user_id

    @admin.display(description='邀请码')
    def invitation_code_text(self, obj):
        return obj.invitation_code_id

    @admin.display(description='对应博主')
    def blogger(self, obj):
        return obj.invitation_code.blogger

    @admin.display(description='注册时间')
    def registration_time(self, obj):
        return obj.registered_at

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('invitation_code')

    def has_module_permission(self, request):
        return (request.user.is_active and request.user.is_staff and
                request.user.has_perm('backend.view_registration_attribution'))

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if request.method not in ('GET', 'HEAD'):
            return HttpResponse(status=405)
        return super().changelist_view(request, extra_context={**(extra_context or {}),
            'can_export_attributions': self.has_module_permission(request) and
                request.user.has_perm('backend.export_registration_attribution')})

    def get_urls(self):
        return [path('export/', self.admin_site.admin_view(self.export_csv),
                     name='backend_registrationattribution_export')] + super().get_urls()

    def export_csv(self, request):
        if not self.has_module_permission(request) or not request.user.has_perm('backend.export_registration_attribution'):
            return HttpResponse(status=403)
        if request.method not in ('GET', 'HEAD'):
            return HttpResponse(status=405)
        # Unknown/duplicate parameters must not turn a failed filter into a full export.
        allowed = {'invitation_code__code', 'invitation_code__code__exact',
            'invitation_code__blogger', 'invitation_code__blogger__exact',
            'registered_at__gte', 'registered_at__lt', 'p', 'o', '_facets'}
        if any(key not in allowed or len(request.GET.getlist(key)) != 1 for key in request.GET):
            return HttpResponse(status=400)
        try:
            records = self.get_changelist_instance(request).queryset
        except (IncorrectLookupParameters, ValidationError, ValueError):
            return HttpResponse(status=400)
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="registration-attributions.csv"'
        response['Cache-Control'] = 'no-store'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['用户编号', '邀请码', '对应博主', '注册时间'])
        for record in records.iterator(chunk_size=500):
            blogger = record.invitation_code.blogger
            if blogger.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                blogger = "'" + blogger
            writer.writerow([record.user_id, record.invitation_code_id, blogger,
                             timezone.localtime(record.registered_at).isoformat()])
        return response
