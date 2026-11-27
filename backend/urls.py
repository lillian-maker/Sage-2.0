from django.urls import path
from django.contrib import admin
from . import desktop_auth, desktop_billing, payments, views

urlpatterns = [path('sales/', admin.site.urls), path('health/live', views.health),
               path('health/ready', views.health, {'readiness': True}),
               path('api/desktop/account', desktop_auth.desktop_account),
               path('api/desktop/billing', desktop_billing.desktop_billing),
               path('api/desktop/orders', desktop_billing.desktop_orders),
               path('api/desktop/orders/<uuid:order_id>', desktop_billing.desktop_order_detail),
               path('api/desktop/subscriptions', desktop_billing.desktop_subscriptions),
               path('api/desktop/usage', desktop_billing.desktop_usage),
               path('api/billing/quotes', views.preview_quote),
               path('api/payments/webhooks/alipay', payments.alipay_sandbox_webhook),
               path('api/payments/webhooks/wechat', payments.wechat_native_webhook),
               path('api/<path:path>', views.api),
               path('', views.public_file), path('<path:path>', views.public_file)]
