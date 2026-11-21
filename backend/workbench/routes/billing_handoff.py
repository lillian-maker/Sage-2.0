"""POST app/billing/handoff (D31): one-time website hand-off credential for 65 加购. Redeemed by GET auth/handoff."""
from ..services import handoff


def issue(request, ctx):
    return handoff.issue(request, ctx)


ROUTES = [
    ('POST', 'billing/handoff', issue),
]
