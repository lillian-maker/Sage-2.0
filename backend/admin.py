"""Sales-only admin: no billing or authentication models are exposed here."""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils.html import format_html, format_html_join

from .consultations import purge_record, sales_access, update_sales
from .models import Consultation, ConsultationAudit

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
