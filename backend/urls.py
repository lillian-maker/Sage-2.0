from django.urls import path
from django.contrib import admin
from . import views

urlpatterns = [path('sales/', admin.site.urls), path('health/live', views.health),
               path('health/ready', views.health, {'readiness': True}),
               path('api/billing/quotes', views.preview_quote),
               path('api/<path:path>', views.api),
               path('', views.public_file), path('<path:path>', views.public_file)]
