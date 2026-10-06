import uuid
import ipaddress
from django.conf import settings
from django.http import JsonResponse


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
        response = self.get_response(request)
        response['X-Request-ID'] = str(request.request_id)
        response['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/'):
            if response.status_code >= 500 and not response.get('Content-Type', '').startswith('application/json'):
                response = error(request, 'SERVICE_UNAVAILABLE', response.status_code)
                response['X-Request-ID'] = str(request.request_id)
                response['X-Frame-Options'] = 'DENY'
            response['Cache-Control'] = 'no-store'
        return response
