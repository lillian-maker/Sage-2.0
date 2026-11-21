"""Router for /api/app/*: every module in backend/workbench/routes/ exposes ROUTES.

ROUTES = [(method, pattern, handler), ...]
  pattern: path after 'app/', e.g. 'tasks/{id}/messages'. Every {name} segment
  matches one public id (ULID). A pattern starting with '^' is a raw regex
  whose named groups become keyword arguments.
  handler(request, ctx, **params) returns a dict (sent as JSON 200), or an
  HttpResponse; raise ValueError(code) for errors registered in views.py.
"""
import importlib
import pkgutil
import re
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured

from ..http import error
from . import common, routes as routes_package
from .ids import PUBLIC_ID_PATTERN

METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
_PLACEHOLDER = re.compile(r'\{([a-z_][a-z0-9_]*)\}')


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    regex: re.Pattern
    handler: object


def _regex(pattern):
    if pattern.startswith('^'):
        return re.compile(pattern)
    parts, last = [], 0
    for match in _PLACEHOLDER.finditer(pattern):
        parts.append(re.escape(pattern[last:match.start()]))
        parts.append('(?P<%s>%s)' % (match.group(1), PUBLIC_ID_PATTERN))
        last = match.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile(''.join(parts))


def compile_routes(entries):
    table, seen = [], set()
    for method, pattern, handler in entries:
        if method not in METHODS or not isinstance(pattern, str) or not callable(handler):
            raise ImproperlyConfigured('INVALID_ROUTE %r %r' % (method, pattern))
        key = (method, pattern)
        if key in seen:
            raise ImproperlyConfigured('DUPLICATE_ROUTE %s %s' % key)
        seen.add(key)
        table.append(Route(method, pattern, _regex(pattern), handler))
    return table


def discover():
    entries = []
    for info in sorted(pkgutil.iter_modules(routes_package.__path__), key=lambda item: item.name):
        module = importlib.import_module('%s.%s' % (routes_package.__name__, info.name))
        entries.extend(getattr(module, 'ROUTES', ()))
    return entries


def build_table():
    return compile_routes(discover())


_table = None


def table():
    global _table
    if _table is None:
        _table = build_table()
    return _table


def resolve(method, subpath):
    allowed, found = [], None
    for route in table():
        match = route.regex.fullmatch(subpath)
        if not match:
            continue
        if route.method not in allowed:
            allowed.append(route.method)
        if route.method == method and found is None:
            found = (route, match.groupdict())
    return found, allowed


def dispatch(request, path):
    """Called from views._api_request after login is enforced; path still has the 'app/' prefix."""
    found, allowed = resolve(request.method, path[len('app/'):])
    if not allowed:
        raise ValueError('NOT_FOUND')
    if found is None:
        response = error(request, 'METHOD_NOT_ALLOWED', 405)
        response['Allow'] = ', '.join(sorted(allowed, key=METHODS.index))
        return response
    ctx = common.context(request)
    if isinstance(ctx, common.Rejection):
        return ctx.response(request)
    route, params = found
    return common.to_response(route.handler(request, ctx, **params))
