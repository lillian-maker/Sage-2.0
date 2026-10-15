"""Only this deployment's gateway and the in-container health probe set HTTPS."""
import os

bind = '0.0.0.0:8000'
workers = 2
worker_class = 'sync'
timeout = 30
graceful_timeout = 30
forwarded_allow_ips = os.environ['SAGE_TRUSTED_PROXY_IPS'] + ',127.0.0.1'
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
forwarder_headers = ''
accesslog = '-'
access_log_format = '%(t)s %(m)s %(U)s %(s)s %(b)s %(L)s'
errorlog = '-'
loglevel = 'info'
capture_output = True
