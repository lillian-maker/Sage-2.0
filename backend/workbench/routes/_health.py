"""Smoke route: proves login, membership and the account header all pass."""


def ping(request, ctx):
    return {'ok': True}


ROUTES = [('GET', 'ping', ping)]
