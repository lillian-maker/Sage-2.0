"""app/search: global search across the member's visible objects."""
from ..common import query
from ..services import search as service
from ..services.inbox import rate_limited, search_text


def search(request, ctx):
    params = query(request, ('q', 'category', 'offset'))
    category = params.get('category', 'all')
    if category not in service.CATEGORIES:
        raise ValueError('INVALID_REQUEST')
    q = search_text(params.get('q', ''))
    limited = rate_limited(request, 'search', 120)
    if limited:
        return limited
    return service.search(ctx, request, q, category)


ROUTES = [
    ('GET', 'search', search),
]
