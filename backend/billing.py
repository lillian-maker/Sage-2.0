"""Read-only billing preflight API. No authentication or money movement yet.

Run locally with: python3 -m backend.billing --port 8900
The standard-library WSGI server is for local development, not production.
"""
import argparse
import json
import mimetypes
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote
from wsgiref.simple_server import make_server

if __package__:
    from .billing_rules import billing_policy
else:
    from billing_rules import billing_policy

ROOT = Path(__file__).resolve().parents[1]


def load_catalog():
    """Validate display data and enforce the disconnected runtime sales gate."""
    try:
        catalog = json.loads((ROOT / 'src/pages/billing-catalog.json').read_text())
    except (OSError, ValueError) as error:
        raise ValueError('CATALOG_INVALID') from error
    if (not isinstance(catalog, dict) or catalog.get('currency') != 'CNY'
            or not isinstance(catalog.get('version'), str) or not catalog['version'].strip()
            or not isinstance(catalog.get('plans'), list) or not catalog['plans']
            or not isinstance(catalog.get('channels'), list)):
        raise ValueError('CATALOG_INVALID')
    plan_codes = set()
    for plan in catalog['plans']:
        if not isinstance(plan, dict):
            raise ValueError('CATALOG_INVALID')
        code = plan.get('code')
        if not isinstance(code, str) or not code or code in plan_codes:
            raise ValueError('CATALOG_INVALID')
        plan_codes.add(code)
        audience, seats = plan.get('audience'), plan.get('minimum_seats')
        if audience not in ('personal', 'business') or type(seats) is not int:
            raise ValueError('CATALOG_INVALID')
        if (audience == 'personal' and seats != 1) or (audience == 'business' and seats < 2):
            raise ValueError('CATALOG_INVALID')
        if type(plan.get('purchasable')) is not bool:
            raise ValueError('CATALOG_INVALID')
        for key in ('monthly_amount_minor', 'annual_amount_minor', 'credits'):
            if key not in plan or (plan[key] is not None and
                                   (type(plan[key]) is not int or plan[key] < 0)):
                raise ValueError('CATALOG_INVALID')
        if code == 'trial':
            if audience != 'personal' or plan['monthly_amount_minor'] != 0 or plan['annual_amount_minor'] is not None:
                raise ValueError('CATALOG_INVALID')
        elif plan['monthly_amount_minor'] == 0 or plan['annual_amount_minor'] == 0:
            raise ValueError('CATALOG_INVALID')
        # Editing a catalog flag must never turn this local service into checkout.
        plan['purchasable'] = False
    channel_codes = set()
    for channel in catalog['channels']:
        if (not isinstance(channel, dict) or not isinstance(channel.get('code'), str)
                or not channel['code'] or channel['code'] in channel_codes
                or type(channel.get('enabled')) is not bool or type(channel.get('recurring')) is not bool):
            raise ValueError('CATALOG_INVALID')
        channel_codes.add(channel['code'])
        channel.update(enabled=False, recurring=False,
                       capabilities={'one_time': False, 'monthly': False,
                                     'annual': False, 'auto_topup': False})
    catalog['checkout_enabled'] = False
    catalog['blockers'] = ['AUTH_NOT_CONNECTED', 'ENTITLEMENTS_NOT_CONFIGURED', 'CHANNEL_NOT_CONNECTED']
    catalog['policy_version'] = billing_policy()['version']
    catalog['policy'] = billing_policy()
    return catalog


def quote(payload):
    if not isinstance(payload, dict) or set(payload) != {'plan_code', 'billing_cycle', 'seat_quantity'}:
        raise ValueError('INVALID_REQUEST')
    if not isinstance(payload['plan_code'], str) or not isinstance(payload['billing_cycle'], str):
        raise ValueError('INVALID_REQUEST')
    catalog = load_catalog()
    plan = next((p for p in catalog['plans'] if p['code'] == payload['plan_code']), None)
    cycle, seats = payload['billing_cycle'], payload['seat_quantity']
    if plan is None or plan['code'] == 'trial' or cycle not in ('monthly', 'annual'):
        raise ValueError('INVALID_REQUEST')
    if type(seats) is not int or seats < plan['minimum_seats'] or seats > 10000:
        raise ValueError('INVALID_SEATS')
    if plan['audience'] == 'personal' and seats != 1:
        raise ValueError('INVALID_SEATS')
    price = plan[cycle + '_amount_minor']
    if price is None:
        raise ValueError('PRICE_NOT_CONFIGURED')
    return {'plan_code': plan['code'], 'billing_cycle': cycle, 'seat_quantity': seats,
            'currency': 'CNY', 'amount_minor': price * seats, 'renewal_amount_minor': price * seats,
            'price_version': catalog['version'], 'preview_only': True, 'purchasable': False,
            'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            'blockers': ['AUTH_NOT_CONNECTED', 'CREDITS_NOT_CONFIGURED', 'CHANNEL_NOT_CONNECTED']}


def respond(start_response, status, data):
    body = json.dumps(data, ensure_ascii=False).encode()
    start_response(f'{status} {HTTPStatus(status).phrase}',
                   [('Content-Type', 'application/json; charset=utf-8'),
                    ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
                    ('X-Content-Type-Options', 'nosniff')])
    return [body]


def app(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    method = environ['REQUEST_METHOD']
    # Local only: reject DNS rebinding and cross-origin POSTs as well as unknown hosts.
    host = environ.get('HTTP_HOST', '')
    if host.split(':')[0] not in ('127.0.0.1', 'localhost'):
        return respond(start_response, 403, {'error': {'code': 'INVALID_HOST'}})
    origin = environ.get('HTTP_ORIGIN')
    if method != 'GET' and origin and origin != 'http://' + host:
        return respond(start_response, 403, {'error': {'code': 'INVALID_ORIGIN'}})
    if path == '/api/billing/catalog' and method == 'GET':
        try:
            data = load_catalog()
        except ValueError:
            return respond(start_response, 503, {'error': {'code': 'CATALOG_INVALID'}})
        return respond(start_response, 200, data)
    if path == '/api/billing/quotes' and method == 'POST':
        if environ.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
            return respond(start_response, 415, {'error': {'code': 'JSON_REQUIRED'}})
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
            if not 0 < length <= 4096:
                return respond(start_response, 413, {'error': {'code': 'INVALID_BODY_SIZE'}})
            payload = json.loads(environ['wsgi.input'].read(length))
            result = quote(payload)
        except (ValueError, UnicodeDecodeError) as error:
            if str(error) == 'CATALOG_INVALID':
                return respond(start_response, 503, {'error': {'code': 'CATALOG_INVALID'}})
            code = str(error) if str(error) in ('INVALID_REQUEST', 'INVALID_SEATS', 'PRICE_NOT_CONFIGURED') else 'INVALID_JSON'
            return respond(start_response, 422, {'error': {'code': code}})
        return respond(start_response, 200, result)
    if path.startswith('/api/payments/webhooks/'):
        return respond(start_response, 503, {'error': {'code': 'CHANNEL_UNAVAILABLE'}})
    if path.startswith('/api/billing/'):
        protected = ('orders', 'subscriptions', 'credits', 'refunds', 'refund-quotes',
                     'invoice-requests', 'payment-method-sessions', 'payment-methods',
                     'auto-topup', 'trial', 'purchase-inquiries')
        if path.split('/')[3] in protected:
            return respond(start_response, 401, {'error': {'code': 'AUTH_REQUIRED'}})
        return respond(start_response, 404, {'error': {'code': 'NOT_FOUND'}})
    if method not in ('GET', 'HEAD'):
        return respond(start_response, 405, {'error': {'code': 'METHOD_NOT_ALLOWED'}})
    relative = unquote(path).lstrip('/') or 'index.html'
    file = (ROOT / relative).resolve()
    allowed = (relative in ('index.html', 'index-en.html') or relative.startswith(('pages/', 'assets/')))
    if not allowed or ROOT not in file.parents or any(p.startswith('.') for p in Path(relative).parts) or not file.is_file():
        return respond(start_response, 404, {'error': {'code': 'NOT_FOUND'}})
    # Never serve paths that normalize out of the public trees, including symlinks.
    if file.relative_to(ROOT).parts[0] not in ('pages', 'assets', 'index.html', 'index-en.html'):
        return respond(start_response, 404, {'error': {'code': 'NOT_FOUND'}})
    body = file.read_bytes()
    start_response('200 OK', [('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream'),
                             ('Content-Length', str(len(body))), ('X-Content-Type-Options', 'nosniff'),
                             ('Cache-Control', 'no-cache')])
    return [] if method == 'HEAD' else [body]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8900)
    args = parser.parse_args()
    with make_server('127.0.0.1', args.port, app) as server:
        print(f'Sage local billing: http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
