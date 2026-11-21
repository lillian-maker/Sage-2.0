"""Bill and invoice documents for the billing screens (64/66 屏，D29).

commerce owns orders, payments and invoice requests; this module only reads them. Discounts are carried in the
order snapshot (discount_minor / discount_label) because commerce does not store them as rows yet. PDFs are
written by hand (one page, built-in Helvetica) so no PDF dependency is needed; their text is ASCII on purpose,
since the standard fonts cannot render CJK without embedding one.
"""
import uuid

from django.http import HttpResponse
from django.utils import timezone

from backend.commerce.models import Order
from backend.models import InvoiceRequest

from .billing import require_role

INVOICE_READY = ('issued', 'sending', 'sent')
DOCUMENTS = ('bill', 'invoice')


def _readable(ctx):
    require_role(ctx, 'owner', 'billing_admin')


def _discount(order):
    snapshot = order.snapshot or {}
    amount = snapshot.get('discount_minor')
    if not amount:
        return None
    return {'amount_minor': int(amount), 'label': snapshot.get('discount_label') or ''}


def _reference_display(reference):
    if not reference:
        return None
    if len(reference) <= 17:
        return '****' + reference[-4:]
    return '%s…%s' % (reference[:13], reference[-4:])


def _needs_review(order):
    return order.payment_facts.exclude(disposition='accepted').exists()


def _invoice_file(order, invoice):
    if invoice is not None and invoice.status in INVOICE_READY:
        return 'available'
    if _needs_review(order):
        return 'after_review'
    return None


def display(ctx):
    _readable(ctx)
    orders = list(Order.objects.filter(account=ctx.account).prefetch_related('payment_facts').order_by('-created_at'))
    invoices = {row.order_id: row for row in InvoiceRequest.objects.filter(account=ctx.account, order__in=orders)}
    channel = None
    rows = []
    for order in orders:
        facts = sorted(order.payment_facts.all(), key=lambda fact: (fact.paid_at, fact.pk))
        accepted = [fact for fact in facts if fact.disposition == 'accepted']
        if accepted and channel is None:
            channel = accepted[-1].channel
        reference = (accepted or facts)[-1].transaction_reference if facts else None
        rows.append({'order_id': str(order.pk), 'discount': _discount(order),
                     'transaction_reference_display': _reference_display(reference),
                     'invoice_file': _invoice_file(order, invoices.get(order.pk))})
    return {'account_id': str(ctx.account.pk), 'payment_channel': channel, 'orders': rows}


def _order(ctx, order_id):
    try:
        key = uuid.UUID(str(order_id))
    except ValueError as exc:
        raise ValueError('NOT_FOUND') from exc
    order = Order.objects.filter(account=ctx.account, pk=key).first()
    if order is None:
        raise ValueError('NOT_FOUND')
    return order


def _money(minor):
    sign = '-' if minor < 0 else ''
    minor = abs(int(minor))
    return 'CNY %s%s.%02d' % (sign, '{:,}'.format(minor // 100), minor % 100)


def _ascii(value):
    return ''.join(ch if 32 <= ord(ch) < 127 else '?' for ch in str(value))


def _item_line(order):
    snapshot = order.snapshot or {}
    if snapshot.get('kind') == 'credits':
        return 'Credits top-up x %s' % '{:,}'.format(int(float(snapshot.get('credits_quantity') or 0)))
    cycle = snapshot.get('billing_cycle') or ''
    return '%s plan%s x %s' % (_ascii(snapshot.get('code') or 'Sage').capitalize(), ' (%s)' % cycle if cycle else '',
                              snapshot.get('seat_quantity') or 1)


def _lines(order, document, invoice):
    snapshot = order.snapshot or {}
    quantity = int(snapshot.get('seat_quantity') or 1)
    unit = snapshot.get('unit_amount_minor')
    subtotal = int(unit) * quantity if unit is not None else int(order.amount_minor)
    discount = _discount(order)
    paid = timezone.localtime(order.paid_at).strftime('%Y-%m-%d %H:%M (%z)') if order.paid_at else 'unpaid'
    facts = list(order.payment_facts.filter(disposition='accepted').order_by('paid_at', 'pk'))
    title = 'Sage - Invoice' if document == 'invoice' else 'Sage - Bill'
    lines = [title, '', 'Bill number: %s' % _ascii(order.display_number or order.pk),
             'Account: %s' % ('Personal' if order.account.kind == 'personal' else 'Business'), 'Paid at: %s' % paid]
    if document == 'invoice':
        lines += ['Invoice reference: %s' % _ascii(invoice.invoice_reference or invoice.pk),
                  'Invoice status: %s' % invoice.status]
    lines += ['', 'Item: %s' % _item_line(order), 'Subtotal: %s' % _money(subtotal)]
    if discount:
        lines.append('Discount: %s' % _money(-discount['amount_minor']))
    lines += ['Total: %s' % _money(order.amount_minor)]
    if facts:
        lines.append('Payment: %s %s' % (facts[-1].channel, '****' + facts[-1].transaction_reference[-4:]))
    return lines


def _escape(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def render_pdf(lines):
    """One A4 page of left-aligned Helvetica lines; returns the complete file as bytes."""
    stream = ['BT', '/F1 12 Tf', '14 TL', '56 780 Td']
    for index, line in enumerate(lines):
        if index:
            stream.append('T*')
        stream.append('(%s) Tj' % _escape(line))
    stream.append('ET')
    content = '\n'.join(stream).encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> '
        b'/Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
        b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream',
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return bytes(out)


def document_pdf(ctx, order_id, document='bill'):
    _readable(ctx)
    if document not in DOCUMENTS:
        raise ValueError('INVALID_REQUEST')
    order = _order(ctx, order_id)
    invoice = InvoiceRequest.objects.filter(order=order).first()
    if document == 'invoice' and (invoice is None or invoice.status not in INVOICE_READY):
        raise ValueError('STATE_CONFLICT')
    if document == 'bill' and not order.paid_at:
        raise ValueError('STATE_CONFLICT')
    response = HttpResponse(render_pdf(_lines(order, document, invoice)), content_type='application/pdf')
    name = '%s-%s.pdf' % ('invoice' if document == 'invoice' else 'bill', _ascii(order.display_number or order.pk))
    response['Content-Disposition'] = 'attachment; filename="%s"' % name
    return response
