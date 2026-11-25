"""Publish reviewed draft product versions; explicit --apply performs the change."""
import json
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from backend.commerce.billing import _quantity, purchase_benefits, purchase_terms
from backend.commerce.models import ProductVersion


class Command(BaseCommand):
    help = ('Check that draft product versions (code:version ...) are complete and their purchase terms '
            'are approved. --apply publishes them effective now; it never edits prices or enables sales.')

    def add_arguments(self, parser):
        parser.add_argument('products', nargs='+')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            keys = []
            for item in options['products']:
                matched = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]{0,79}):([1-9][0-9]{0,9})', item)
                if not matched or (matched.group(1), int(matched.group(2))) in keys:
                    raise ValueError('INVALID_PRODUCT_REFERENCE')
                keys.append((matched.group(1), int(matched.group(2))))
            published, unchanged = [], 0
            with transaction.atomic():
                now = timezone.now()
                for code, version in keys:
                    product = ProductVersion.objects.filter(code=code, version=version)
                    if options['apply']:
                        product = product.select_for_update()
                    product = product.first()
                    if not product:
                        raise ValueError('PRODUCT_NOT_FOUND')
                    if product.state == 'published':
                        unchanged += 1
                        continue
                    if product.state != 'draft':
                        raise ValueError('PRODUCT_STATE_CONFLICT')
                    if (product.currency != 'CNY' or type(product.unit_amount_minor) is not int or
                            product.unit_amount_minor <= 0 or not product.entitlement_version or
                            not product.credit_rule_version or not product.terms_version or
                            product.kind not in ('subscription', 'credits') or
                            (product.kind == 'subscription') != (product.billing_cycle in ('monthly', 'annual')) or
                            (product.kind == 'credits' and product.billing_cycle)):
                        raise ValueError('PRODUCT_INCOMPLETE')
                    _quantity(product.credits_quantity, positive=product.kind == 'credits')
                    purchase_benefits(product)
                    purchase_terms(product.terms_version)
                    if options['apply']:
                        product.state, product.effective_at = 'published', now
                        product.save(update_fields=['state', 'effective_at'])
                    published.append(f'{code}:{version}')
        except ValueError as exc:
            code = str(exc) if re.fullmatch(r'[A-Z_]+', str(exc)) else 'PRODUCT_PUBLISH_INVALID'
            raise CommandError(code) from exc
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'validate',
                                     'publish': published, 'unchanged': unchanged}))
