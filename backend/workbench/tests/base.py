"""Shared fixtures for workbench (app/) API tests.

Usage:
    class TaskTests(AppTestCase):
        def test_list(self):
            self.login(self.lillian, self.business)
            body = self.ok(self.get('app/tasks'))

Default world (setUpTestData): personal account per user, plus the business
account "Sage Demo 企业" where Lillian is owner, Chen Yu is billing_admin and
Mark Weber is member. Anna Koch is a "guest": a real user who is NOT a member
of the business account (Q-P1), so app/ calls with her session return 404.
"""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.models import BillingAccount, LoginIdentity, Membership

API = '/api/'


def make_user(email, first_name='', last_name='', personal=True):
    user = get_user_model().objects.create_user(
        username=uuid.uuid4().hex, email=email, first_name=first_name, last_name=last_name,
        password='Fixture!864')
    LoginIdentity.objects.create(user=user, method='email', identifier=email)
    if personal:
        account = BillingAccount.objects.create(kind='personal', name='个人账户', personal_owner=user)
        Membership.objects.create(account=account, user=user, role='owner')
    return user


def make_business(name='Sage Demo 企业', members=()):
    """members: iterable of (user, role) with role in owner/billing_admin/member."""
    account = BillingAccount.objects.create(kind='business', name=name)
    for user, role in members:
        Membership.objects.create(account=account, user=user, role=role)
    return account


class AppClientMixin:
    """JSON helpers that hit /api/<path> and send X-Billing-Account by default."""
    account_header = True

    def login(self, user, account=None, client=None):
        client = client or self.client
        client.force_login(user)
        if account is None:
            account = BillingAccount.objects.get(personal_owner=user)
        session = client.session
        session['active_account_id'] = str(account.pk)
        session.save()
        return account

    def _headers(self, client, account, extra):
        headers = dict(extra)
        if account is not False and 'HTTP_X_BILLING_ACCOUNT' not in headers:
            value = account if account is not None else client.session.get('active_account_id')
            if value:
                headers['HTTP_X_BILLING_ACCOUNT'] = str(getattr(value, 'pk', value))
        return headers

    def request(self, method, path, data=None, *, account=None, client=None, raw=None, **extra):
        """account=None uses the session account, False omits the header, otherwise sends it."""
        client = client or self.client
        headers = self._headers(client, account, extra)
        url = API + path.lstrip('/')
        if method == 'GET':
            return client.get(url, data or {}, **headers)
        body = raw if raw is not None else (json.dumps(data) if data is not None else '')
        return client.generic(method, url, body, content_type='application/json', **headers)

    def get(self, path, params=None, **kwargs):
        return self.request('GET', path, params, **kwargs)

    def post(self, path, data=None, **kwargs):
        return self.request('POST', path, {} if data is None else data, **kwargs)

    def patch(self, path, data=None, **kwargs):
        return self.request('PATCH', path, {} if data is None else data, **kwargs)

    def put(self, path, data=None, **kwargs):
        return self.request('PUT', path, {} if data is None else data, **kwargs)

    def delete(self, path, data=None, **kwargs):
        return self.request('DELETE', path, data, **kwargs)

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content[:500])
        return response.json()

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content[:500])
        body = response.json()
        self.assertEqual(body['error']['code'], code)
        self.assertIn('request_id', body)
        return body


class AppTestCase(AppClientMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lillian = make_user('lillian@example.com', 'Lillian', 'Li')
        cls.chen = make_user('chen@sage.demo', 'Chen', 'Yu')
        cls.mark = make_user('mark@sage.demo', 'Mark', 'Weber')
        cls.anna = make_user('anna@partner.de', 'Anna', 'Koch')
        cls.business = make_business(members=((cls.lillian, 'owner'), (cls.chen, 'billing_admin'),
                                              (cls.mark, 'member')))
        cls.personal = BillingAccount.objects.get(personal_owner=cls.lillian)

    def member(self, user, account=None):
        return Membership.objects.get(user=user, account=account or self.business)
