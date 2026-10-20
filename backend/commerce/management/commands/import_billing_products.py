"""Validate commercial JSON; explicit --apply inserts immutable-version drafts only."""
import json
import re
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from backend.commerce.billing import _quantity, purchase_benefits
from backend.commerce.models import ProductVersion


class Command(BaseCommand):
    help = 'Validate a JSON product list. --apply creates drafts only; never publishes, overwrites or enables sales.'

    def add_arguments(self, parser):
        parser.add_argument('manifest')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        fields = {'code', 'version', 'kind', 'audience', 'billing_cycle', 'currency',
            'unit_amount_minor', 'credits_quantity', 'minimum_seats', 'entitlements',
            'entitlement_version', 'credit_rule_version', 'terms_version'}

        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('DUPLICATE_JSON_KEY')
                result[key] = value
            return result

        def invalid_constant(value):
            raise ValueError('INVALID_JSON_NUMBER')

        try:
            path = Path(options['manifest'])
            if path.suffix.lower() != '.json':
                raise ValueError('JSON_MANIFEST_REQUIRED')
            data = path.read_bytes()
            if len(data) > 1048576:
                raise ValueError('MANIFEST_TOO_LARGE')
            rows = json.loads(data.decode('utf-8'), object_pairs_hook=object_pairs,
                              parse_constant=invalid_constant)
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise ValueError('INVALID_PRODUCT_LIST')
            keys, products = set(), []
            for row in rows:
                if not isinstance(row, dict) or set(row) != fields:
                    raise ValueError('INVALID_PRODUCT_FIELDS')
                for field in ('code', 'entitlement_version', 'credit_rule_version', 'terms_version'):
                    if not isinstance(row[field], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', row[field]):
                        raise ValueError('INVALID_PRODUCT_IDENTIFIER')
                if (type(row['version']) is not int or not 1 <= row['version'] <= 2147483647
                    or type(row['minimum_seats']) is not int or not 1 <= row['minimum_seats'] <= 2147483647
                    or type(row['unit_amount_minor']) is not int or not 1 <= row['unit_amount_minor'] <= 9007199254740991
                    or row['currency'] != 'CNY' or not isinstance(row['credits_quantity'], str)
                    or not isinstance(row['entitlements'], dict)):
                    raise ValueError('INVALID_PRODUCT_VALUES')
                quantity = _quantity(row['credits_quantity'], positive=row['kind'] == 'credits')
                if row['kind'] == 'subscription':
                    if row['billing_cycle'] not in ('monthly', 'annual'):
                        raise ValueError('INVALID_BILLING_CYCLE')
                elif row['kind'] != 'credits' or row['billing_cycle'] != '':
                    raise ValueError('INVALID_PRODUCT_KIND')
                if row['code'] in {'teams', 'enterprise'}:
                    expected_mode = 'seat_included' if row['code'] == 'teams' else 'shared_included'
                    if (row['audience'] != 'business' or row['kind'] != 'subscription' or
                            row['entitlements'].get('resource_mode') != expected_mode):
                        raise ValueError('INVALID_BUSINESS_RESOURCES')
                if row['audience'] == 'personal' and row['minimum_seats'] != 1:
                    raise ValueError('INVALID_MINIMUM_SEATS')
                if row['audience'] == 'business' and row['kind'] == 'subscription':
                    mode = row['entitlements'].get('resource_mode')
                    if (mode not in ('seat_included', 'shared_included') or quantity <= 0 or
                            (mode == 'shared_included' and row['minimum_seats'] < 10)):
                        raise ValueError('INVALID_BUSINESS_RESOURCES')
                key = (row['code'], row['version'])
                if key in keys:
                    raise ValueError('DUPLICATE_PRODUCT_VERSION')
                keys.add(key)
                product = ProductVersion(**row, state='draft')
                # A draft intentionally has no activation date; published-sale
                # validation remains in the existing independent sale gate.
                product.full_clean(exclude=['effective_at'], validate_unique=False, validate_constraints=False)
                purchase_benefits(product)
                products.append(product)
            created, unchanged = 0, 0
            with transaction.atomic():
                for product in products:
                    existing = ProductVersion.objects.filter(code=product.code, version=product.version)
                    if options['apply']:
                        existing = existing.select_for_update()
                    existing = existing.first()
                    if existing:
                        if any(getattr(existing, field) != getattr(product, field) for field in fields):
                            raise ValueError('PRODUCT_VERSION_CONFLICT')
                        unchanged += 1
                    else:
                        if options['apply']:
                            product.save(force_insert=True)
                        created += 1
        except (OSError, UnicodeError, ValueError, ValidationError, IntegrityError) as exc:
            # Do not echo commercial input, paths, database detail or arbitrary JSON.
            code = str(exc) if type(exc) is ValueError and re.fullmatch(r'[A-Z_]+', str(exc)) else 'PRODUCT_IMPORT_INVALID'
            raise CommandError(code) from exc
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'validate',
                                     'create': created, 'unchanged': unchanged}))
