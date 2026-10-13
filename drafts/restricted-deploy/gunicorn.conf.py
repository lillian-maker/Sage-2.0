"""受限验收配置；仅回环监听，不启用真实供应商能力。"""
bind = '127.0.0.1:8000'
workers = 2
worker_class = 'sync'
timeout = 30
graceful_timeout = 30
forwarded_allow_ips = '127.0.0.1'
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
forwarder_headers = ''
accesslog = '-'
access_log_format = '%(t)s %(m)s %(U)s %(s)s %(b)s %(L)s'
errorlog = '-'
loglevel = 'info'
capture_output = True
