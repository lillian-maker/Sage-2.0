"""企业管理 people side table (D41): GET app/org/people, PUT app/org/invitations/{id}/profile."""
from .. import common
from ..services import org_people


def people(request, ctx):
    common.query(request)
    return org_people.people(ctx)


def invitation_profile(request, ctx, iid):
    return org_people.set_invitation_profile(ctx, iid, common.json_body(request, ('display_name',)))


ROUTES = [
    ('GET', 'org/people', people),
    ('PUT', '^org/invitations/(?P<iid>[^/]+)/profile$', invitation_profile),
]
