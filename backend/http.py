import uuid
import ipaddress
import logging
import re
from django.conf import settings
from django.db import DatabaseError, transaction
from django.http import JsonResponse


class OAuthQueryFilter(logging.Filter):
    """Django runserver request lines include the one-time callback query."""
    def filter(self, record):
        def redact(value):
            return re.sub(r'/api/auth/google/callback\?[^\s"\']*',
                '/api/auth/google/callback',value) if isinstance(value,str) else value
        record.msg = redact(record.msg)
        if isinstance(record.args,dict):
            for key,value in list(record.args.items()):
                record.args[key] = '' if key=='q' and '/api/auth/google/callback' in str(record.args.get('r','')) else redact(value)
        elif isinstance(record.args,tuple):
            record.args = tuple(redact(value) for value in record.args)
        return True


def client_ip(request):
    peer = request.META.get('REMOTE_ADDR', '')
    candidate = request.META.get('HTTP_X_REAL_IP', '') if peer in settings.TRUSTED_PROXY_IPS else peer
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        try:
            return str(ipaddress.ip_address(peer))
        except ValueError:
            return ''


def error(request, code, status=400, message='请求未完成，请稍后重试。'):
    return JsonResponse({'error': {'code': code, 'message': message},
                         'request_id': str(getattr(request, 'request_id', uuid.uuid4()))}, status=status)


def csrf_failure(request, reason=''):
    return error(request, 'CSRF_FAILED', 403, '会话校验失败，请刷新页面重试。')


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4()
        if request.method == 'POST' and request.path == '/api/billing/accounts/select':
            # This middleware surrounds SessionMiddleware. Keep the view's
            # session-row lock until the normal session/cookie save completes.
            try:
                with transaction.atomic():
                    response = self.get_response(request)
                    if response.status_code >= 500:
                        transaction.set_rollback(True)
            except DatabaseError:
                logging.getLogger('sage').error('account_selection_database_unavailable request_id=%s',request.request_id)
                response = error(request,'DATABASE_UNAVAILABLE',503)
        else:
            response = self.get_response(request)
        if request.path == '/api/auth/google/callback':
            # META is the WSGI environ used later by Gunicorn's r/q atoms.
            request.META['QUERY_STRING'] = ''
            for key in ('RAW_URI','REQUEST_URI'):
                if key in request.META:
                    request.META[key] = request.path
            response['Referrer-Policy'] = 'no-referrer'
        response['X-Request-ID'] = str(request.request_id)
        response['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/'):
            if response.status_code >= 500 and not response.get('Content-Type', '').startswith('application/json'):
                response = error(request, 'SERVICE_UNAVAILABLE', response.status_code)
                response['X-Request-ID'] = str(request.request_id)
                response['X-Frame-Options'] = 'DENY'
            response['Cache-Control'] = 'no-store'
        return response
