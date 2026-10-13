"""Report stage-two configuration only, without DB access or provider requests."""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from backend.authentication import captcha_ready, registration_ready, registration_terms_ready
from backend.google_auth import bind_email_ready, ready as google_ready
from backend.views import email_ready, email_transport_ready, password_reset_ready


class Command(BaseCommand):
    help = 'Check registration/login configuration without sending messages or testing suppliers.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true', help='Exit nonzero for missing stage-two configuration.')

    def handle(self, *args, **options):
        try:
            callback_host_matches = bool(google_ready() and
                urlsplit(settings.GOOGLE_REDIRECT_URI).hostname in settings.ALLOWED_HOSTS)
        except (TypeError, ValueError):
            callback_host_matches = False
        checks = {
            'captcha': captcha_ready(),
            'email_transport': email_transport_ready(),
            'legal_approval_declared': registration_terms_ready(),
            'email_registration': registration_ready(),
            'password_login': captcha_ready(),
            'password_recovery': captcha_ready() and password_reset_ready(),
            'password_change_notification': settings.AUTH_EMAIL_TRANSPORT == 'ses' and
                password_reset_ready() and email_ready(settings.AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID),
            'google_login': google_ready(),
            'google_registration': google_ready() and registration_terms_ready(),
            'google_bind_email': bind_email_ready(),
            'google_callback_host': callback_host_matches,
        }
        blockers = [name for name, ready in checks.items() if not ready]
        self.stdout.write(json.dumps({
            'evidence': 'configuration_only', 'external_requests': 0, 'database_access': False,
            'configuration_ready': not blockers, 'ready_for_public_launch': False,
            'checks': checks, 'blockers': blockers,
            'unverified': ['legal_content_and_approval', 'captcha_challenge_and_server_verification',
                'provider_template_approval', 'mailbox_delivery_and_recovery', 'google_real_account_flows',
                'https_routes_and_cookies', 'native_password_manager'],
        }, indent=2))
        if options['strict'] and blockers:
            raise CommandError('Authentication configuration is incomplete; see named blockers above.')
