from django.urls import path
from django.contrib import admin
from . import payments, views

urlpatterns = [path('sales/', admin.site.urls), path('health/live', views.health),
               path('health/ready', views.health, {'readiness': True}),
               path('api/billing/quotes', views.preview_quote),
               path('api/payments/webhooks/alipay', payments.alipay_sandbox_webhook),
               path('api/payments/webhooks/wechat', payments.wechat_native_webhook),
               path('api/<path:path>', views.api),
               path('', views.public_file), path('<path:path>', views.public_file)]
