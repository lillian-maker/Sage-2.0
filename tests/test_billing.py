"""Public HTTP contract tests; no payment provider or real money involved."""
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from wsgiref.simple_server import make_server
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.billing import app


class BillingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server('127.0.0.1', 0, app)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, data=None, headers=None):
        request = Request(self.base + path,
                          data=None if data is None else json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            # These are loopback tests; never route forged-Host cases through OS proxies.
            response = build_opener(ProxyHandler({})).open(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_catalog_exposes_confirmed_prices_not_sellable_products(self):
        status, data = self.request('/api/billing/catalog')
        self.assertEqual(status, 200)
        plans = {plan['code']: plan for plan in data['plans']}
        self.assertEqual(plans['pro']['monthly_amount_minor'], 19900)
        self.assertEqual(plans['pro_plus']['monthly_amount_minor'], 49900)
        self.assertEqual(plans['ultra']['monthly_amount_minor'], 199000)
        self.assertIsNone(plans['pro']['annual_amount_minor'])
        self.assertEqual(plans['teams']['minimum_seats'], 1)
        self.assertEqual(plans['enterprise']['minimum_seats'], 10)
        self.assertIsNone(plans['enterprise']['credits'])
        self.assertTrue(all(not plan['purchasable'] for plan in plans.values()))

    def test_quote_is_server_priced_preview_not_an_order(self):
        status, data = self.request('/api/billing/quotes',
                                    {'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': 1})
        self.assertEqual(status, 200)
        self.assertEqual(data['amount_minor'], 19900)
        self.assertEqual(data['renewal_amount_minor'], 19900)
        self.assertFalse(data['purchasable'])
        self.assertTrue(data['preview_only'])
        self.assertNotIn('order_id', data)

    def test_unconfigured_prices_and_invalid_seats_are_rejected(self):
        for payload, code in [
            ({'plan_code': 'pro', 'billing_cycle': 'annual', 'seat_quantity': 1}, 'PRICE_NOT_CONFIGURED'),
            ({'plan_code': 'teams', 'billing_cycle': 'monthly', 'seat_quantity': 0}, 'INVALID_SEATS'),
            ({'plan_code': 'teams', 'billing_cycle': 'monthly', 'seat_quantity': 1}, 'PRICE_NOT_CONFIGURED'),
            ({'plan_code': 'teams', 'billing_cycle': 'monthly', 'seat_quantity': 2}, 'PRICE_NOT_CONFIGURED'),
            ({'plan_code': 'enterprise', 'billing_cycle': 'monthly', 'seat_quantity': 9}, 'INVALID_SEATS'),
            ({'plan_code': 'enterprise', 'billing_cycle': 'monthly', 'seat_quantity': 10}, 'PRICE_NOT_CONFIGURED'),
            ({'plan_code': 'enterprise', 'billing_cycle': 'monthly', 'seat_quantity': 11}, 'PRICE_NOT_CONFIGURED'),
            ({'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': True}, 'INVALID_SEATS'),
            ({'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': 2}, 'INVALID_SEATS'),
            ({'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': 1, 'amount_minor': 1}, 'INVALID_REQUEST'),
        ]:
            with self.subTest(payload=payload):
                status, data = self.request('/api/billing/quotes', payload)
                self.assertEqual(status, 422)
                self.assertEqual(data['error']['code'], code)

    def test_orders_cannot_bypass_missing_identity_with_claimed_user_id(self):
        status, data = self.request('/api/billing/orders', {'user_id': 'owner', 'quote_id': 'fake'})
        self.assertEqual(status, 401)
        self.assertEqual(data['error']['code'], 'AUTH_REQUIRED')

    def test_unverified_webhooks_never_acknowledge_payment(self):
        status, data = self.request('/api/payments/webhooks/alipay', {'status': 'succeeded'})
        self.assertEqual(status, 503)
        self.assertEqual(data['error']['code'], 'CHANNEL_UNAVAILABLE')

    def test_cross_origin_quote_rejected(self):
        status, _ = self.request('/api/billing/quotes', {}, {'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)

    def test_private_paths_and_traversal_are_not_served(self):
        for path in ['/backend/billing.py', '/.git/config', '/pages/../README.md', '/pages/%2e%2e/backend/billing.py']:
            with self.subTest(path=path):
                status, _ = self.request(path)
                self.assertEqual(status, 404)

    def test_oversize_body_is_rejected(self):
        status, data = self.request('/api/billing/quotes', {'value': 'x' * 5000})
        self.assertEqual(status, 413)
        self.assertEqual(data['error']['code'], 'INVALID_BODY_SIZE')

    def test_non_object_json_is_rejected_without_server_failure(self):
        for payload in [[], 'pro', 19900, True]:
            with self.subTest(payload=payload):
                status, data = self.request('/api/billing/quotes', payload)
                self.assertEqual(status, 422)
                self.assertEqual(data['error']['code'], 'INVALID_REQUEST')

    def test_foreign_host_is_rejected(self):
        status, _ = self.request('/api/billing/catalog', headers={'Host':'evil.example'})
        self.assertEqual(status, 403)

    def test_catalog_exposes_rules_without_account_or_payment_state(self):
        status, data = self.request('/api/billing/catalog')
        self.assertEqual(status, 200)
        data = data['policy']
        self.assertEqual(data['version'], '2026-10-16.2')
        self.assertEqual(data['currency'], 'CNY')
        self.assertEqual(data['renewal']['manual_payment_window_days'], 3)
        self.assertFalse(data['renewal']['automatic_retry'])
        self.assertTrue(data['auto_topup']['separate_authorization'])
        self.assertNotIn('balance', data)
        self.assertNotIn('user_id', data)

    def test_catalog_flags_cannot_enable_real_checkout(self):
        catalog = json.loads((Path(__file__).resolve().parents[1] / 'src/pages/billing-catalog.json').read_text())
        for plan in catalog['plans']:
            plan['purchasable'] = True
        for channel in catalog['channels']:
            channel.update(enabled=True, recurring=True)
        with patch('backend.billing.Path.read_text', return_value=json.dumps(catalog)):
            status, data = self.request('/api/billing/catalog')
            self.assertEqual(status, 200)
            self.assertFalse(data['checkout_enabled'])
            self.assertTrue(all(not plan['purchasable'] for plan in data['plans']))
            self.assertTrue(all(not channel['enabled'] and not any(channel['capabilities'].values())
                                for channel in data['channels']))
            status, data = self.request('/api/billing/quotes',
                                       {'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': 1})
            self.assertEqual(status, 200)
            self.assertFalse(data['purchasable'])
            self.assertTrue(data['preview_only'])

    def test_corrupt_catalog_fails_closed_without_null_becoming_free(self):
        original = json.loads((Path(__file__).resolve().parents[1] / 'src/pages/billing-catalog.json').read_text())
        invalid = []
        for key, value in [('monthly_amount_minor', True), ('monthly_amount_minor', 0),
                           ('annual_amount_minor', 0), ('credits', -1), ('minimum_seats', True)]:
            data = json.loads(json.dumps(original))
            data['plans'][1][key] = value
            invalid.append(data)
        invalid.extend([{**original, 'currency': 'USD'}, {**original, 'version': ''}, []])
        for code, minimum in [('teams', 0), ('enterprise', 9)]:
            data = json.loads(json.dumps(original))
            next(plan for plan in data['plans'] if plan['code'] == code)['minimum_seats'] = minimum
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), patch('backend.billing.Path.read_text', return_value=json.dumps(data)):
                status, body = self.request('/api/billing/catalog')
                self.assertEqual(status, 503)
                self.assertEqual(body['error']['code'], 'CATALOG_INVALID')
                status, body = self.request('/api/billing/quotes',
                                           {'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': 1})
                self.assertEqual(status, 503)
        with patch('backend.billing.Path.read_text', return_value='broken-json'):
            self.assertEqual(self.request('/api/billing/catalog')[0], 503)

    def test_new_write_families_do_not_create_fake_state_without_identity(self):
        for path in ('payment-methods/card/remove', 'auto-topup', 'trial/start', 'purchase-inquiries',
                     'credits/reservations', 'subscriptions/demo/cancel-renewal'):
            with self.subTest(path=path):
                status, body = self.request('/api/billing/' + path, {'user_id': 'fake-owner'})
                self.assertEqual(status, 401)
                self.assertEqual(body['error']['code'], 'AUTH_REQUIRED')

    def test_quote_strict_types_and_cny_only(self):
        base = {'plan_code': 'pro', 'billing_cycle': 'monthly', 'seat_quantity': 1}
        for data in ({**base, 'plan_code': ['pro']}, {**base, 'billing_cycle': ['monthly']},
                     {**base, 'seat_quantity': 1.5}, {**base, 'currency': 'USD'}):
            with self.subTest(data=data):
                self.assertEqual(self.request('/api/billing/quotes', data)[0], 422)


if __name__ == '__main__':
    unittest.main()
