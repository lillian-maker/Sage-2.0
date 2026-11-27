"""Read-only personal commerce DTOs for `/api/desktop/*` (BR-20).

Every read derives the personal `BillingAccount` from the authenticated identity;
no request parameter can name an account. The endpoints reuse the existing
commerce read services and expose amounts in integer minor units with `CNY`
where the ledger stores them. Nothing here writes, prices, invoices or charges.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .commerce.billing import account_summary, credit_transactions_page, order_detail, orders_page
from .commerce.subscriptions import list_subscriptions
from .desktop_auth import DesktopAuthError, authenticate_desktop_request
from .models import BillingAccount, InvoiceRequest

SOURCE = 'django-commerce'


def _refused(code, status):
    return JsonResponse({'state': 'refused', 'code': code}, status=status)


def _personal_account(user):
    account = BillingAccount.objects.filter(kind='personal', personal_owner=user).first()
    if account is None:
        raise DesktopAuthError('desktop-account-not-provisioned', 403)
    return account


def _offset(request):
    if any(key != 'offset' for key in request.GET):
        raise DesktopAuthError('desktop-request-invalid', 400)
    raw = request.GET.get('offset', '0')
    if not raw.isdigit() or len(raw) > 9:
        raise DesktopAuthError('desktop-request-invalid', 400)
    return int(raw)


def _no_query(request):
    if request.GET:
        raise DesktopAuthError('desktop-request-invalid', 400)


def _read(request, handler):
    if request.method != 'GET':
        return _refused('desktop-method-not-allowed', 405)
    try:
        user = authenticate_desktop_request(request)
    except DesktopAuthError as error:
        return _refused(error.code, error.status)
    try:
        payload = handler(user, _personal_account(user), request)
    except DesktopAuthError as error:
        return _refused(error.code, error.status)
    except ValueError as error:
        if str(error) == 'NOT_FOUND':
            return _refused('desktop-order-not-found', 404)
        return _refused('desktop-request-invalid', 400)
    return JsonResponse({'state': 'read', 'source': SOURCE, 'currency': 'CNY', **payload})


@csrf_exempt
def desktop_billing(request):
    def read(user, account, http_request):
        _no_query(http_request)
        summary = account_summary(account)
        return {
            'account': {'id': str(account.pk), 'displayName': user.first_name or user.username},
            'credits': summary['credits'],
            'capabilities': summary['capabilities'],
            'subscriptions': list_subscriptions(account),
        }
    return _read(request, read)


@csrf_exempt
def desktop_orders(request):
    def read(user, account, http_request):
        return orders_page(account, _offset(http_request))
    return _read(request, read)


@csrf_exempt
def desktop_order_detail(request, order_id):
    def read(user, account, http_request):
        _no_query(http_request)
        payload = order_detail(account, order_id)
        invoice = InvoiceRequest.objects.filter(order_id=payload['order']['id']).first()
        return {**payload, 'invoice': None if invoice is None else {
            'status': invoice.status, 'updatedAt': invoice.updated_at.isoformat()}}
    return _read(request, read)


@csrf_exempt
def desktop_subscriptions(request):
    def read(user, account, http_request):
        _no_query(http_request)
        return {'items': list_subscriptions(account)}
    return _read(request, read)


@csrf_exempt
def desktop_usage(request):
    def read(user, account, http_request):
        return credit_transactions_page(account, _offset(http_request))
    return _read(request, read)
