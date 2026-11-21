"""Shared request helpers for app/ route handlers."""
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse

from ..http import error
from ..views import membership

PAGE_SIZE = 50
DEFAULT_BODY_LIMIT = 64 * 1024


@dataclass(frozen=True)
class AppContext:
    member: object
    account: object
    user: object


@dataclass(frozen=True)
class Rejection:
    code: str
    status: int
    message: str = '请求未完成，请稍后重试。'

    def response(self, request):
        return error(request, self.code, self.status, self.message)


def context(request):
    """Active membership of the session account; non-members see 404 so account ids are not probeable."""
    member = membership(request)
    if member is None:
        return Rejection('NOT_FOUND', 404)
    expected = request.headers.get('X-Billing-Account')
    if (request.method not in ('GET', 'HEAD') and not expected) or (expected and expected != str(member.account_id)):
        return Rejection('ACCOUNT_CHANGED', 409, '当前计费账户已改变，请刷新确认后重新操作。')
    return AppContext(member=member, account=member.account, user=request.user)


def _read_body(request, limit):
    # request.body enforces settings.DATA_UPLOAD_MAX_MEMORY_SIZE (8 KB, kept for
    # legacy APIs); reading the stream directly lets each route choose its own cap.
    if hasattr(request, '_body'):
        raw = request._body
    else:
        try:
            length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError as exc:
            raise ValueError('INVALID_JSON') from exc
        if length > limit:
            raise ValueError('INVALID_BODY_SIZE')
        raw = request.read(limit + 1)
        request._body = raw
    if len(raw) > limit:
        raise ValueError('INVALID_BODY_SIZE')
    return raw


def json_body(request, fields, optional=(), limit=DEFAULT_BODY_LIMIT):
    """Same rules as views.payload (exact keys, JSON object) with a per-route size cap."""
    if request.content_type != 'application/json':
        raise ValueError('JSON_REQUIRED')
    raw = _read_body(request, limit)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError('INVALID_JSON') from exc
    if not isinstance(data, dict) or not set(fields) <= set(data) or set(data) - set(fields) - set(optional):
        raise ValueError('INVALID_REQUEST')
    return data


def query(request, allowed=()):
    """Reject unknown or repeated query keys; returns a plain dict of single values."""
    keys = set(request.GET)
    if keys - set(allowed) or any(len(request.GET.getlist(key)) > 1 for key in keys):
        raise ValueError('INVALID_REQUEST')
    return {key: request.GET[key] for key in keys}


def offset(request):
    raw = request.GET.get('offset', '0')
    if len(request.GET.getlist('offset')) > 1 or not re.fullmatch(r'[0-9]{1,9}', raw):
        raise ValueError('INVALID_REQUEST')
    return int(raw)


def page(request, items, serialize):
    """Fixed 50-row pages: {items, offset, total, next_offset}. items may be a QuerySet or a list."""
    start = offset(request)
    total = items.count() if hasattr(items, 'count') and not isinstance(items, list) else len(items)
    rows = [serialize(item) for item in items[start:start + PAGE_SIZE]]
    return {'items': rows, 'offset': start, 'total': total,
            'next_offset': start + PAGE_SIZE if start + PAGE_SIZE < total else None}


def iso(value):
    return value.isoformat() if value is not None else None


class AppJSONEncoder(DjangoJSONEncoder):
    """Full isoformat for datetimes (DjangoJSONEncoder truncates to milliseconds and uses 'Z')."""

    def default(self, value):
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return super().default(value)


def respond(data, status=200):
    return JsonResponse(data, status=status, encoder=AppJSONEncoder)


def to_response(result):
    if isinstance(result, HttpResponse):
        return result
    if isinstance(result, dict):
        return respond(result)
    raise TypeError('app handlers must return a dict or HttpResponse')
