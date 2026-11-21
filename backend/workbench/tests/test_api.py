import json
from unittest import mock

from django.test import SimpleTestCase

from backend.workbench import api, common
from backend.workbench.ids import PUBLIC_ID_PATTERN, new_public_id
from backend.workbench.tests.base import AppTestCase

import re


def _echo(request, ctx, **params):
    return {'account': str(ctx.account.pk), 'role': ctx.member.role, 'params': params}


def _body(request, ctx):
    return {'data': common.json_body(request, ('name',), optional=('note',))}


def _big(request, ctx):
    return {'size': len(common.json_body(request, ('text',), limit=100 * 1024)['text'])}


def _paged(request, ctx):
    common.query(request, allowed=('offset',))
    return common.page(request, list(range(120)), lambda value: {'n': value})


def _boom(code):
    def handler(request, ctx, **params):
        raise ValueError(code)
    return handler


TEST_ROUTES = [
    ('GET', 'test/things/{id}', _echo),
    ('PATCH', 'test/things/{id}', _echo),
    ('GET', 'test/things/{id}/items/{item_id}', _echo),
    ('POST', 'test/body', _body),
    ('POST', 'test/big', _big),
    ('GET', 'test/paged', _paged),
] + [('POST', 'test/error/' + code.lower(), _boom(code)) for code in (
    'INSUFFICIENT_CREDITS', 'TASK_RESUME_FORBIDDEN', 'TASK_COMPLETED', 'LIMIT_CHANGED', 'STATE_CONFLICT',
    'VERSION_CONFLICT', 'AUTHORIZATION_REQUIRED', 'AUTHORIZATION_EXPIRED', 'CONNECTION_REQUIRED',
    'POLICY_APPROVAL_REQUIRED', 'INVALID_LIMIT_VERSION', 'QUOTA_BELOW_USED', 'SUCCESSOR_INVALID',
    'VALIDATION_FAILED', 'PERMISSION_DENIED', 'EXTENSION_NOT_ALLOWED', 'SOURCE_UNAVAILABLE')]

ERROR_STATUS = {
    'INSUFFICIENT_CREDITS': 409, 'TASK_RESUME_FORBIDDEN': 409, 'TASK_COMPLETED': 409, 'LIMIT_CHANGED': 409,
    'STATE_CONFLICT': 409, 'VERSION_CONFLICT': 409, 'AUTHORIZATION_REQUIRED': 409,
    'AUTHORIZATION_EXPIRED': 409, 'CONNECTION_REQUIRED': 409, 'POLICY_APPROVAL_REQUIRED': 409,
    'INVALID_LIMIT_VERSION': 422, 'QUOTA_BELOW_USED': 422, 'SUCCESSOR_INVALID': 422,
    'VALIDATION_FAILED': 422, 'PERMISSION_DENIED': 403, 'EXTENSION_NOT_ALLOWED': 403,
    'SOURCE_UNAVAILABLE': 503,
}


class PublicIdTests(SimpleTestCase):
    def test_ulid_shape_and_order(self):
        first, second = new_public_id(1_700_000_000_000), new_public_id(1_700_000_000_001)
        self.assertRegex(first, '^' + PUBLIC_ID_PATTERN + '$')
        self.assertLess(first[:10], second[:10])
        self.assertNotEqual(new_public_id(), new_public_id())

    def test_rejects_out_of_range_time(self):
        with self.assertRaises(ValueError):
            new_public_id(-1)


class RouteTableTests(SimpleTestCase):
    def test_discovers_health_module(self):
        table = api.build_table()
        self.assertTrue(any(route.method == 'GET' and route.pattern == 'ping' for route in table))

    def test_duplicate_route_is_rejected(self):
        with self.assertRaisesRegex(Exception, 'DUPLICATE_ROUTE'):
            api.compile_routes([('GET', 'a/{id}', _echo), ('GET', 'a/{id}', _echo)])

    def test_unknown_method_is_rejected(self):
        with self.assertRaisesRegex(Exception, 'INVALID_ROUTE'):
            api.compile_routes([('FETCH', 'a', _echo)])


class AppDispatchContractTests(AppTestCase):
    def setUp(self):
        patcher = mock.patch.object(api, '_table', api.compile_routes(api.discover() + TEST_ROUTES))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ping_requires_login(self):
        self.assert_error(self.get('app/ping', account=False), 401, 'AUTH_REQUIRED')

    def test_ping(self):
        self.login(self.lillian, self.business)
        self.assertEqual(self.ok(self.get('app/ping')), {'ok': True})

    def test_unknown_path_is_404(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.get('app/nope'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/test/things/not-a-ulid'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/ping/'), 404, 'NOT_FOUND')

    def test_wrong_method_is_405_with_allow(self):
        self.login(self.lillian, self.business)
        response = self.delete('app/test/things/' + new_public_id())
        self.assert_error(response, 405, 'METHOD_NOT_ALLOWED')
        self.assertEqual(response['Allow'], 'GET, PATCH')

    def test_path_params_and_context(self):
        self.login(self.mark, self.business)
        thing, item = new_public_id(), new_public_id()
        body = self.ok(self.get('app/test/things/%s/items/%s' % (thing, item)))
        self.assertEqual(body, {'account': str(self.business.pk), 'role': 'member',
                                'params': {'id': thing, 'item_id': item}})

    def test_non_member_gets_404(self):
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/ping'), 404, 'NOT_FOUND')

    def test_inactive_member_gets_404(self):
        member = self.member(self.mark)
        member.is_active = False
        member.save()
        self.login(self.mark, self.business)
        self.assert_error(self.get('app/ping'), 404, 'NOT_FOUND')

    def test_write_requires_matching_account_header(self):
        self.login(self.lillian, self.business)
        path = 'app/test/things/' + new_public_id()
        self.assert_error(self.patch(path, account=False), 409, 'ACCOUNT_CHANGED')
        self.assert_error(self.patch(path, account=self.personal), 409, 'ACCOUNT_CHANGED')
        self.ok(self.patch(path))

    def test_read_with_stale_header_is_409(self):
        self.login(self.lillian, self.business)
        self.ok(self.get('app/ping', account=False))
        self.assert_error(self.get('app/ping', account=self.personal), 409, 'ACCOUNT_CHANGED')

    def test_json_body_rules(self):
        self.login(self.lillian, self.business)
        self.assertEqual(self.ok(self.post('app/test/body', {'name': 'a'})), {'data': {'name': 'a'}})
        self.assert_error(self.post('app/test/body', {}), 422, 'INVALID_REQUEST')
        self.assert_error(self.post('app/test/body', {'name': 'a', 'x': 1}), 422, 'INVALID_REQUEST')
        self.assert_error(self.post('app/test/body', raw='{bad'), 422, 'INVALID_JSON')
        self.assert_error(self.post('app/test/body', raw='[]'), 422, 'INVALID_REQUEST')
        response = self.client.post('/api/app/test/body', 'name=a', content_type='text/plain',
                                    HTTP_X_BILLING_ACCOUNT=str(self.business.pk))
        self.assert_error(response, 422, 'JSON_REQUIRED')

    def test_body_limit_defaults_to_64kb(self):
        self.login(self.lillian, self.business)
        self.ok(self.post('app/test/body', {'name': 'x' * 60000}))
        self.assert_error(self.post('app/test/body', {'name': 'x' * 70000}), 422, 'INVALID_BODY_SIZE')

    def test_route_can_raise_body_limit(self):
        self.login(self.lillian, self.business)
        self.assertEqual(self.ok(self.post('app/test/big', {'text': 'x' * 90000})), {'size': 90000})

    def test_pagination_shape(self):
        self.login(self.lillian, self.business)
        first = self.ok(self.get('app/test/paged'))
        self.assertEqual((len(first['items']), first['offset'], first['total'], first['next_offset']),
                         (50, 0, 120, 50))
        self.assertEqual(first['items'][0], {'n': 0})
        last = self.ok(self.get('app/test/paged', {'offset': '100'}))
        self.assertEqual((len(last['items']), last['next_offset']), (20, None))
        for bad in ('-1', 'a', '1234567890', ''):
            self.assert_error(self.get('app/test/paged', {'offset': bad}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/test/paged', {'limit': '10'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.client.get('/api/app/test/paged?offset=0&offset=50'), 422, 'INVALID_REQUEST')

    def test_registered_error_codes(self):
        self.login(self.lillian, self.business)
        for code, status in ERROR_STATUS.items():
            with self.subTest(code=code):
                self.assert_error(self.post('app/test/error/' + code.lower()), status, code)


class SerializationTests(SimpleTestCase):
    def test_datetime_isoformat(self):
        from datetime import datetime, timezone as tz
        value = datetime(2026, 11, 3, 9, 42, 0, 123456, tzinfo=tz.utc)
        response = common.respond({'at': value})
        self.assertEqual(json.loads(response.content)['at'], value.isoformat())
        self.assertIsNone(common.iso(None))
