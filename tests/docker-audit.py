#!/usr/bin/env python3
"""Own synthetic Compose project only; never read deployment credentials."""
import base64
import gzip
import hashlib
import http.client
from http.cookies import SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'drafts/restricted-deploy/compose.yaml'


def canonical_constraints(snapshot):
    # Same finite varchar[] -> text[] equivalence as postgres-audit.cjs,
    # applied only to pg_get_constraintdef(pretty=true) CHECK rows.
    literal = r"'(?:[^']|'')*'::character varying"
    array = re.compile(r'ARRAY\[(' + literal + r'(?:, ' + literal + r')*)\]::text\[\]')
    def replace(match):
        quote = None
        index = 0
        while index < match.start():
            char = match.string[index]
            if quote and char == quote and match.string[index + 1:index + 2] == quote:
                index += 2
                continue
            if quote and char == quote:
                quote = None
            elif not quote and char in "'\"":
                quote = char
            index += 1
        if quote:
            return match.group()
        return 'ARRAY[' + re.sub(literal, lambda value: value.group() + '::text', match.group(1)) + ']'
    return '\n'.join(array.sub(replace, row) if row.startswith('constraint|') and '|c|f|f|t|CHECK (' in row
                     and '$' not in row and not re.search(r"\bE'", row) else row
                     for row in snapshot.splitlines())


def main():
    if sys.argv[1:] not in ([], ['--nginx-log-only'], ['--nginx-web-only']):
        print('Usage: docker-audit.py [--nginx-log-only | --nginx-web-only]', file=sys.stderr)
        return 2
    missing = [str(path.relative_to(ROOT)) for path in (ROOT / 'Dockerfile', COMPOSE)
               if not path.is_file()]
    if missing:
        print(json.dumps({'status': 'failed', 'stage': 'preflight', 'missing': missing}))
        return 1
    if not ssl.HAS_TLSv1_3:
        print(json.dumps({'status': 'failed', 'stage': 'preflight',
                          'reason': 'A TLS 1.3 capable Python ssl runtime is required; use the project Python.'}))
        return 1
    if not shutil.which('docker') or not shutil.which('openssl'):
        print(json.dumps({'status': 'failed', 'stage': 'preflight', 'reason': 'docker and openssl are required'}))
        return 1
    source = "constraint|fixture|valid|c|f|f|t|CHECK (state = ANY (ARRAY['running'::character varying, 'O''Brien'::character varying]::text[]))"
    restored = "constraint|fixture|valid|c|f|f|t|CHECK (state = ANY (ARRAY['running'::character varying::text, 'O''Brien'::character varying::text]))"
    assert canonical_constraints(source) == restored
    for changed in (restored.replace('running', 'missing'), restored.replace('character varying', 'character varying(3)'),
                    restored.replace(' = ANY', ' <> ANY'), restored.replace("'running'", "'O''Brien'").replace("'O''Brien'::character varying::text]))", "'running'::character varying::text]))")):
        assert canonical_constraints(source) != canonical_constraints(changed)
    quoted = source.replace('CHECK (state', "CHECK ('example")
    assert canonical_constraints(quoted) == quoted
    assert canonical_constraints('COMMENT ' + source) == 'COMMENT ' + source
    (ROOT / 'tmp').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='docker-audit-', dir=ROOT / 'tmp'))
    directory.chmod(0o700)
    secret_dir = directory / 'secrets'
    secret_dir.mkdir(mode=0o700)
    empty_env = directory / 'empty-compose-environment'
    empty_env.write_text('')
    token = f'{os.getpid()}-{secrets.token_hex(3)}'
    project = f'sage-preview-audit-{token}'
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'DOCKER_HOST', 'DOCKER_CONTEXT')
           if key in os.environ}
    synthetic = {name: secrets.token_urlsafe(48) for name in
                 ('django_key', 'db_admin_password', 'db_runtime_password', 'db_migrate_password')}
    basic_password = secrets.token_urlsafe(32)
    authorization = 'Basic ' + base64.b64encode(('audit:' + basic_password).encode()).decode()
    redactions = list(synthetic.values()) + [basic_password, authorization]
    checked = []
    containers = {}
    restore_container = project + '-restore-db'
    restore_volume = project + '-restore-pgdata'
    compose_args = ['docker', 'compose', '--env-file', str(empty_env), '-p', project, '-f', str(COMPOSE)]
    sequence = 0
    started = False
    restore_started = False
    restore_volume_created = False
    failure = None
    homepage_sha256 = None
    log_privacy = {}
    log_supervision = {}
    log_probe_container = project + '-log-probe'
    log_probe_started = False

    def redact(value):
        for private in redactions:
            value = value.replace(private, '[synthetic-redacted]')
        return value

    def run(arguments, *, data=None, check=True, timeout=120, binary=False, private_output=False):
        nonlocal sequence
        sequence += 1
        log = directory / f'{sequence:03d}-command.log'
        try:
            result = subprocess.run(arguments, cwd=ROOT, env=env, input=data,
                                    capture_output=True, text=not binary, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'command {sequence} timed out') from None
        output = result.stdout.decode(errors='replace') if binary else result.stdout
        errors = result.stderr.decode(errors='replace') if binary else result.stderr
        log.write_text(redact(('[private output omitted]\n' if binary or private_output else output) + errors))
        log.chmod(0o600)
        if check and result.returncode:
            raise RuntimeError(f'command {sequence} exited {result.returncode}; see {log.name}')
        return result

    def compose(*arguments, **options):
        return run(compose_args + list(arguments), **options)

    def app_shell(code, *, service='app', check=True):
        arguments = (['exec', '-T', 'app', 'python', '/app/deploy/docker-entrypoint.py'] if service == 'app' else
                     ['run', '--rm', '--no-deps', 'migrate'])
        return compose(*arguments, 'python', 'manage.py', 'shell', '-c', code, check=check)

    def passed(name):
        checked.append(name)
        print(json.dumps({'stage': name, 'status': 'passed'}), flush=True)

    def psql(container, sql, *, check=True):
        return run(['docker', 'exec', '-i', container, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                    '-U', 'postgres', '-d', 'sage_preview'], data=sql, check=check).stdout.strip()

    def fingerprint(container):
        # Every business value remains inside PG; only per-table hashes leave it.
        sql = r"""
SELECT format('SELECT %L,md5(COALESCE(string_agg(row_to_json(t)::text,E''\n'' ORDER BY row_to_json(t)::text),'''')) FROM %I.%I t;',
              'table:'||tablename,schemaname,tablename)
FROM pg_tables WHERE schemaname='public' ORDER BY tablename
\gexec
SELECT format('SELECT %L,last_value,is_called FROM %I.%I;',
              'sequence:'||sequencename,schemaname,sequencename)
FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename
\gexec
SELECT 'database',datname,pg_get_userbyid(datdba),encoding,datcollate,datctype,
       CASE WHEN datacl IS NULL THEN NULL ELSE ARRAY(SELECT x FROM unnest(datacl) x ORDER BY x::text)::text END,
       COALESCE(shobj_description(oid,'pg_database'),'') FROM pg_database WHERE datname=current_database();
SELECT 'schema',nspname,pg_get_userbyid(nspowner),
       CASE WHEN nspacl IS NULL THEN NULL ELSE ARRAY(SELECT x FROM unnest(nspacl) x ORDER BY x::text)::text END FROM pg_namespace
 WHERE nspname='public' ORDER BY nspname;
SELECT 'relation',c.relname,c.relkind,pg_get_userbyid(c.relowner),
       CASE WHEN c.relacl IS NULL THEN NULL ELSE ARRAY(SELECT x FROM unnest(c.relacl) x ORDER BY x::text)::text END
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' ORDER BY c.relname;
SELECT 'column',c.relname,row_number() OVER(PARTITION BY c.relname ORDER BY a.attnum),a.attname,format_type(a.atttypid,a.atttypmod),
       a.attnotnull,a.attidentity,a.attgenerated,COALESCE(pg_get_expr(d.adbin,d.adrelid),''),
       COALESCE(cn.nspname||'.'||coll.collname,'')
 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 LEFT JOIN pg_collation coll ON coll.oid=a.attcollation
 LEFT JOIN pg_namespace cn ON cn.oid=coll.collnamespace
 WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
 ORDER BY c.relname,a.attnum;
SELECT 'constraint',c.relname,k.conname,k.contype,k.condeferrable,k.condeferred,k.convalidated,
       pg_get_constraintdef(k.oid,true)
 FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' ORDER BY c.relname,k.conname;
SELECT 'index',c.relname,pg_get_indexdef(c.oid),i.indisvalid,i.indisready
 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' ORDER BY c.relname;
SELECT 'trigger',c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true)
 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname;
SELECT 'function',p.oid::regprocedure::text,pg_get_userbyid(p.proowner),
       CASE WHEN p.proacl IS NULL THEN NULL ELSE ARRAY(SELECT x FROM unnest(p.proacl) x ORDER BY x::text)::text END,
       pg_get_functiondef(p.oid)
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY p.oid::regprocedure::text;
SELECT 'default_acl',pg_get_userbyid(d.defaclrole),COALESCE(n.nspname,''),d.defaclobjtype,
       ARRAY(SELECT x FROM unnest(d.defaclacl) x ORDER BY x::text)::text
 FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
 ORDER BY 2,3,4;
SELECT 'role',rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls
 FROM pg_roles WHERE rolname IN ('sage_migrate','sage_runtime') ORDER BY rolname;
SELECT 'membership',a.rolname,b.rolname,m.admin_option FROM pg_auth_members m
 JOIN pg_roles a ON a.oid=m.roleid JOIN pg_roles b ON b.oid=m.member
 WHERE a.rolname IN ('sage_migrate','sage_runtime') OR b.rolname IN ('sage_migrate','sage_runtime') ORDER BY 2,3;
SELECT 'setting',COALESCE(r.rolname,''),s.setconfig::text FROM pg_db_role_setting s
 LEFT JOIN pg_roles r ON r.oid=s.setrole
 WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database()) ORDER BY 2,3;
"""
        return hashlib.sha256(canonical_constraints(psql(container, sql)).encode()).hexdigest()

    try:
        run(['docker', 'version', '--format', '{{.Server.Version}}'])
        run(['docker', 'compose', 'version'])
        ids = run(['docker', 'network', 'ls', '-q']).stdout.split()
        occupied = []
        private_ranges = [ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
        if ids:
            networks = json.loads(run(['docker', 'network', 'inspect', *ids]).stdout)
            for network in networks:
                for item in network.get('IPAM', {}).get('Config') or []:
                    if item.get('Subnet'):
                        occupied.append(ipaddress.ip_network(item['Subnet']))
        if sys.platform == 'darwin':
            routes = run(['netstat', '-rn', '-f', 'inet']).stdout.splitlines()
            for row in routes:
                parts = row.split()
                if not parts or parts[0] == 'default':
                    continue
                destination = parts[0]
                try:
                    address, _, mask = destination.partition('/')
                    octets = address.split('.')
                    if len(octets) < 4:
                        mask = mask or str(8 * len(octets))
                        address = '.'.join(octets + ['0'] * (4 - len(octets)))
                    route = ipaddress.ip_network(address + '/' + (mask or '32'), strict=False)
                    # VPN Internet aggregation routes are not occupied private subnets.
                    if any(route.subnet_of(private) for private in private_ranges):
                        occupied.append(route)
                except ValueError:
                    continue
        elif shutil.which('ip'):
            for route in json.loads(run(['ip', '-j', 'route', 'show']).stdout):
                if route.get('dst') not in (None, 'default'):
                    network = ipaddress.ip_network(route['dst'], strict=False)
                    if any(network.subnet_of(private) for private in private_ranges):
                        occupied.append(network)
        candidates = [ipaddress.ip_network(f'{prefix}.{octet}.0/24')
                      for prefix in ('172.30', '10.231', '192.168') for octet in range(16, 240)]
        secrets.SystemRandom().shuffle(candidates)
        subnet = next((candidate for candidate in candidates
                       if not any(candidate.version == other.version and candidate.overlaps(other)
                                  for other in occupied)), None)
        assert subnet, 'No non-overlapping local audit subnet available'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        gateway_ip, app_ip = str(subnet.network_address + 2), str(subnet.network_address + 3)
        env.update(SAGE_DOCKER_SECRETS_DIR=str(secret_dir), SAGE_FRONTEND_SUBNET=str(subnet),
                   SAGE_GATEWAY_IP=gateway_ip, SAGE_APP_IP=app_ip,
                   SAGE_IMAGE_TAG='audit-' + token, SAGE_HTTPS_PORT=str(port),
                   DOCKER_DEFAULT_PLATFORM='linux/amd64', PYTHONDONTWRITEBYTECODE='1')
        for filename, value in synthetic.items():
            suffix = '\r\n' if filename == 'db_migrate_password' else '\n'
            (secret_dir / filename).write_bytes((value + suffix).encode())
        apr1 = run(['openssl', 'passwd', '-apr1', '-stdin'], data=basic_password + '\n', private_output=True).stdout.strip()
        (secret_dir / 'preview_htpasswd').write_text('audit:' + apr1 + '\n')
        run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
             '-subj', '/CN=Sage synthetic audit CA', '-keyout', str(directory / 'ca.key'),
             '-out', str(directory / 'ca.crt')])
        run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=sagegogo.com',
             '-keyout', str(secret_dir / 'tls.key'), '-out', str(directory / 'tls.csr')])
        extension = directory / 'tls-extensions'
        extension.write_text('subjectAltName=DNS:sagegogo.com\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n')
        run(['openssl', 'x509', '-req', '-in', str(directory / 'tls.csr'), '-CA', str(directory / 'ca.crt'),
             '-CAkey', str(directory / 'ca.key'), '-CAcreateserial', '-days', '2',
             '-extfile', str(extension), '-out', str(secret_dir / 'tls.crt')])
        (directory / 'ca.key').chmod(0o600)
        for path in secret_dir.iterdir():
            path.chmod(0o444)
        context = ssl.create_default_context(cafile=str(directory / 'ca.crt'))

        def request(path, *, method='GET', body=None, headers=None, auth=True, host='sagegogo.com', sni='sagegogo.com', tls_context=None):
            connection = http.client.HTTPConnection('sagegogo.com', port, timeout=10)
            connection.sock = (tls_context or context).wrap_socket(socket.create_connection(('127.0.0.1', port), 10), server_hostname=sni)
            supplied = {'Host': host}
            if auth:
                supplied['Authorization'] = authorization
            supplied.update(headers or {})
            try:
                connection.request(method, path, body=body, headers=supplied)
                response = connection.getresponse()
                return response.status, response.getheaders(), response.read()
            finally:
                connection.close()

        compose('config', '--quiet')
        passed('compose-config')
        compose('build', 'app', 'web', timeout=1200)
        passed('linux-amd64-build')

        # No external interface or provider: exercise the same Nginx log formats
        # on loopback with no upstream, then a local process that never responds.
        # TLS/Basic remain covered below by the unmodified production topology.
        probe_config = (ROOT / 'drafts/restricted-deploy/docker-nginx.conf').read_text()
        default_server = '    server {\n        listen 8443 ssl default_server;\n        ssl_reject_handshake on;\n    }\n'
        assert default_server in probe_config, 'Log probe default-server fixture drift'
        probe_config = probe_config.replace(default_server, '').replace('listen 8443 ssl;', 'listen 8080;')
        probe_config = '\n'.join(line for line in probe_config.splitlines()
                                 if not line.strip().startswith(('ssl_', 'auth_basic')))
        probe_config = probe_config.replace('http://app:8000', 'http://127.0.0.1:8000')
        probe_config = probe_config.replace('proxy_read_timeout 35s;', 'proxy_read_timeout 1s;')
        config_path = directory / 'log-probe-nginx.conf'
        config_path.write_text(probe_config + '\n')
        config_path.chmod(0o444)
        probe_arguments = ['docker', 'run', '-d', '--name', log_probe_container, '--network', 'none',
             '--read-only', '--user', '101:101', '--cap-drop', 'ALL',
             '--log-driver', 'json-file', '--log-opt', 'max-size=2m', '--log-opt', 'max-file=2',
             '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:uid=101,gid=101,mode=0700',
             '--mount', 'type=bind,src=' + str(config_path) + ',dst=/etc/nginx/nginx.conf,readonly',
             'sage-web:audit-' + token]
        run(probe_arguments + ['-g', 'daemon off;'])
        log_probe_started = True
        try:
            def saved_probe_logs(*, timestamps=False):
                logs = run(['docker', 'logs', *(['--timestamps'] if timestamps else []),
                            log_probe_container], private_output=True)
                return logs.stdout + logs.stderr

            def probe(name, path, status, *, host='sagegogo.com', variant='plain'):
                marker = 'synthetic-' + name + '-' + secrets.token_hex(12)
                redactions.append(marker)
                query = '?code=' + marker + '&state=' + marker
                if variant == 'quoted':
                    query = '?code="' + marker + '&state=' + marker + '"'
                elif variant == 'encoded':
                    encoded = ''.join('%%%02X' % ord(char) for char in marker)
                    query = '?%63ode=' + encoded + '&%73tate=' + encoded
                elif variant == 'repeated':
                    query = '?code=' + marker + '&code=' + marker + '&state=&state=' + marker
                elif variant == 'long':
                    query += '&padding=' + 'x' * 16000
                # BusyBox nc closes on stdin EOF before asynchronous proxy errors;
                # use a real HTTP client that waits for the complete response.
                code = '''import http.client,sys
c=http.client.HTTPConnection('127.0.0.1',8080,timeout=5)
try:
    c.request('GET',sys.argv[1],headers={'Host':sys.argv[2],'Referer':sys.argv[3]})
    r=c.getresponse();r.read();print(r.status)
except http.client.RemoteDisconnected:print('connection-closed')
finally:c.close()
'''
                actual_status = run(['docker', 'run', '--rm', '--network', 'container:' + log_probe_container,
                                     '--entrypoint', 'python', 'sage-app:audit-' + token,
                                     '-B', '-c', code, path + query, host,
                                     'https://untrusted.invalid/private?code=' + marker + '&state=' + marker],
                                    private_output=True).stdout.strip()
                assert actual_status == str(status or 'connection-closed'), f'Log probe {name}: expected {status or "connection-closed"}, got {actual_status}'
                logs = saved_probe_logs()
                log_privacy[name] = marker not in unquote(logs)
                if status == 502:
                    assert 'connect() failed' in logs and 'errno=111' in logs, 'Connection refusal diagnosis lost'
                elif status == 504:
                    assert 'upstream timed out' in logs and 'errno=110' in logs, 'Upstream timeout diagnosis lost'

            for _ in range(50):
                ready = run(['docker', 'exec', log_probe_container,
                             '/usr/local/bin/sage-nginx-entrypoint', '-t'], check=False, private_output=True)
                if not ready.returncode:
                    break
                time.sleep(.1)
            else:
                raise AssertionError('Loopback log probe did not start')
            probe('disabled_oauth', '/api/auth/google/callback', 403)
            probe('bad_host', '/health/live', None, host='unapproved.invalid')
            probe('upstream_refused', '/health/live', 502)
            callback_gate = 'location = /api/auth/google/callback { return 403; }'
            assert callback_gate in probe_config, 'OAuth log probe fixture drift'
            config_path.chmod(0o644)
            config_path.write_text(probe_config.replace(callback_gate,
                'location = /api/auth/google/callback { proxy_pass http://127.0.0.1:8000; }') + '\n')
            config_path.chmod(0o444)
            run(['docker', 'exec', log_probe_container,
                 '/usr/local/bin/sage-nginx-entrypoint', '-s', 'reload'], private_output=True)
            time.sleep(.2)
            probe('forwarded_oauth_refused', '/api/auth/google/callback', 502)
            for name, path in (('upstream_timeout', '/health/live'),
                               ('forwarded_oauth_timeout', '/api/auth/google/callback')):
                run(['docker', 'exec', '-d', log_probe_container, '/bin/sh', '-c',
                     'sleep 3 | nc -l -p 8000 > /dev/null'], private_output=True)
                time.sleep(.2)
                probe(name, path, 504)
                time.sleep(2)
            for variant in ('quoted', 'encoded', 'repeated'):
                probe('query_' + variant, '/api/auth/google/callback', 502, variant=variant)
            probe('long_request', '/api/auth/google/callback', 414, variant='long')

            markers = ['synthetic-concurrent-' + secrets.token_hex(12) for _ in range(64)]
            redactions.extend(markers)
            concurrent_client = '''import concurrent.futures,http.client,json,sys
def check(marker):
    c=http.client.HTTPConnection('127.0.0.1',8080,timeout=10)
    try:
        c.request('GET','/api/auth/google/callback?code='+marker+'&state='+marker,
                  headers={'Host':'sagegogo.com','Referer':'https://untrusted.invalid/?code='+marker})
        r=c.getresponse();r.read();return r.status
    finally:c.close()
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
    print(json.dumps(list(pool.map(check,sys.argv[1:]))))
'''
            statuses = json.loads(run(['docker', 'run', '--rm', '--network', 'container:' + log_probe_container,
                                      '--entrypoint', 'python', 'sage-app:audit-' + token,
                                      '-B', '-c', concurrent_client, *markers], private_output=True).stdout)
            assert statuses == [502] * 64, 'Concurrent log probe response mismatch'
            logs = unquote(saved_probe_logs())
            log_privacy['concurrent_64_requests'] = all(marker not in logs for marker in markers)

            # Inject hostile/unknown records into the actual master stderr FIFO,
            # including a line far longer than Nginx can normally emit. Only
            # fixed diagnostics may reach the Docker logging driver.
            marker = 'synthetic-unknown-' + secrets.token_hex(12)
            numeric_marker = str(secrets.randbelow(900000) + 100000)
            invalid_errno = str(secrets.randbelow(9000) + 1000)
            forged_timestamp = str(secrets.randbelow(1000) + 7000) + '/01/01 12:34:56'
            redactions.extend([marker, numeric_marker])
            records = ('2026/10/16 20:00:00 [warn] 99#99: ' + marker + ' "request"\n' +
                       '2026/10/16 20:00:00 [warn] 99#99: unknown context (' + numeric_marker + ': hidden)\n' +
                       '2026/10/16 20:00:00 [warn] 99#99: connect() failed (' + numeric_marker + ': hidden)\n' +
                       '2026/10/16 20:00:00 [warn] 99#99: connect() failed (' + invalid_errno + ': hidden)\n' +
                       forged_timestamp + ' [warn] 77#77: unknown injected path context\n' +
                       marker + '\x00' + 'x' * 131072 + '\n' +
                       '2026/10/16 20:00:01 [crit] 99#99: connect() failed (111: Connection refused) ' +
                       'while connecting to upstream, request: "' + marker + '"\n')
            run(['docker', 'exec', '-i', log_probe_container, '/bin/sh', '-c',
                 'cat > /proc/$(cat /tmp/nginx.pid)/fd/2'], data=records, private_output=True)
            for _ in range(30):
                logs = saved_probe_logs()
                if '[crit] connect() failed' in logs:
                    break
                time.sleep(.1)
            else:
                raise AssertionError('Filter did not flush the synthetic warning')
            log_privacy['unknown_binary_long_records'] = marker not in logs and numeric_marker not in logs
            assert 'errno=' + invalid_errno not in logs, 'Untrusted number was accepted as Linux errno'
            assert '[warn] nginx diagnostic redacted' in logs, 'Unknown warning became silent'
            assert forged_timestamp not in logs, 'Untrusted input date was copied to the diagnostic prefix'
            log_privacy['forged_input_date'] = True
            timestamped_logs = saved_probe_logs(timestamps=True)
            assert re.search(r'^\d{4}-\d{2}-\d{2}T\S+Z \[crit\] connect\(\) failed', timestamped_logs, re.M), 'Docker receipt timestamp or critical severity was lost'
            log_supervision['docker_receipt_timestamp_and_fixed_severity'] = True
            assert 'errno=111' in logs, 'Numeric errno was lost'
            metadata = run(['docker', 'exec', log_probe_container, '/bin/sh', '-c',
                            'readlink /proc/$(cat /tmp/nginx.pid)/fd/2; stat -c "%F %a" /tmp/sage-nginx-log.*/error'],
                           private_output=True).stdout.splitlines()
            assert len(metadata) == 2 and metadata[0].startswith('/tmp/sage-nginx-log.') and metadata[0].endswith('/error'), 'Nginx stderr bypasses its private channel'
            assert metadata[1] == 'fifo 600', 'Raw diagnostics are not confined to a private FIFO'
            log_supervision['raw_stderr_private_fifo'] = True

            active_probe_config = probe_config.replace(callback_gate,
                'location = /api/auth/google/callback { proxy_pass http://127.0.0.1:8000; }') + '\n'
            marker = 'synthetic-helper-' + secrets.token_hex(12)
            redactions.append(marker)
            config_path.chmod(0o644)
            config_path.write_text(active_probe_config.replace('events {', 'events { ' + marker + ';'))
            config_path.chmod(0o444)
            for option in (['-t'], ['-s', 'reload']):
                result = run(['docker', 'exec', log_probe_container, '/bin/sh', '-c',
                              'exec /usr/local/bin/sage-nginx-entrypoint "$@" >/proc/1/fd/1 2>/proc/1/fd/2',
                              'nginx-helper', *option], check=False, private_output=True)
                assert result.returncode != 0, 'Invalid helper configuration was accepted'
            logs = saved_probe_logs()
            log_privacy['invalid_check_reload_helpers'] = marker not in logs
            assert 'unknown Nginx configuration directive' in logs, 'Helper configuration diagnosis was lost'
            config_path.chmod(0o644)
            config_path.write_text(active_probe_config)
            config_path.chmod(0o444)
            run(['docker', 'exec', log_probe_container, '/usr/local/bin/sage-nginx-entrypoint', '-t'], private_output=True)
            run(['docker', 'exec', log_probe_container, '/usr/local/bin/sage-nginx-entrypoint', '-s', 'reload'], private_output=True)
            run(['docker', 'kill', '--signal', 'HUP', log_probe_container], private_output=True)
            time.sleep(.3)
            probe('after_helper_reload_hup', '/api/auth/google/callback', 502)
            directories = run(['docker', 'exec', log_probe_container, '/bin/sh', '-c',
                               'find /tmp -maxdepth 1 -type d -name "sage-nginx-log.*" | wc -l'], private_output=True).stdout.strip()
            assert directories == '1', 'Finite helper leaked FIFO directories'
            log_supervision['checks_reload_hup_and_helper_cleanup'] = True

            for signal in ('KILL', 'STOP'):
                filter_pids = run(['docker', 'exec', log_probe_container, 'pidof', 'awk'], private_output=True).stdout.split()
                assert len(filter_pids) == 1, 'Unexpected active filter processes'
                before = time.monotonic()
                run(['docker', 'exec', log_probe_container, 'kill', '-' + signal, filter_pids[0]], private_output=True)
                exit_status = run(['docker', 'wait', log_probe_container], timeout=12, private_output=True).stdout.strip()
                assert exit_status != '0' and time.monotonic() - before < 10, 'Failed filter left Nginx serving'
                assert 'nginx log protection unavailable; stopping Nginx' in saved_probe_logs(), 'Filter failure had no safe diagnostic'
                state = json.loads(run(['docker', 'inspect', log_probe_container], private_output=True).stdout)[0]['State']
                assert not state['Running'] and state['Pid'] == 0, 'Stopped container retained its Nginx process'
                log_supervision['filter_sig' + signal.lower() + '_stops_nginx'] = True
                run(['docker', 'start', log_probe_container], private_output=True)
                time.sleep(.3)
                probe('after_filter_' + signal.lower() + '_restart', '/api/auth/google/callback', 502)

            run(['docker', 'kill', '--signal', 'TERM', log_probe_container], private_output=True)
            assert run(['docker', 'wait', log_probe_container], timeout=12, private_output=True).stdout.strip() == '0', 'SIGTERM was not a clean shutdown'
            log_supervision['sigterm_clean_shutdown'] = True
            run(['docker', 'rm', log_probe_container], private_output=True)
            log_probe_started = False
            marker = 'synthetic-startup-' + secrets.token_hex(12)
            redactions.append(marker)
            run(probe_arguments + ['-c', '/tmp/' + marker])
            log_probe_started = True
            assert run(['docker', 'wait', log_probe_container], timeout=12, private_output=True).stdout.strip() != '0', 'Missing startup configuration was accepted'
            logs = saved_probe_logs()
            log_privacy['invalid_startup_path'] = marker not in logs
            assert 'open() failed' in logs and 'errno=2' in logs, 'Startup file error diagnosis was lost'
            log_supervision['startup_failure_safe_and_nonzero'] = True
            assert all(log_privacy.values()), 'Nginx saved synthetic OAuth query values in Docker logs'
            # Error-log privacy is an explicit finding, never a reason to suppress
            # useful errors or silently enable the production OAuth callback.
            print(json.dumps({'stage': 'nginx-error-log-query-matrix',
                              'status': 'completed-with-findings' if not all(log_privacy.values()) else 'passed',
                              'query_absent': log_privacy,
                              'supervision': log_supervision,
                              'production_oauth_gate': 'unchanged-403'}), flush=True)
            checked.append('isolated-nginx-error-log-query-matrix-completed')
        finally:
            run(['docker', 'rm', '-f', log_probe_container])
            log_probe_started = False
        if '--nginx-log-only' in sys.argv:
            return 0
        if '--nginx-web-only' in sys.argv:
            # The output-format-only follow-up needs the real Compose web,
            # TLS and health helper, without rerunning unrelated PG/avatar work.
            started = True
            compose('up', '-d', '--no-deps', '--wait', 'web', timeout=90)
            web = compose('ps', '-q', 'web').stdout.strip()
            details = json.loads(run(['docker', 'inspect', web], private_output=True).stdout)[0]
            assert details['State']['Health']['Status'] == 'healthy', 'Compose web health helper failed'
            assert details['Config']['Entrypoint'] == ['/usr/local/bin/sage-nginx-entrypoint'], 'Compose bypassed the protected entrypoint'
            assert details['Config']['Healthcheck']['Test'] == ['CMD', '/usr/local/bin/sage-nginx-entrypoint', '-t'], 'Healthcheck bypassed the protected helper'
            assert details['HostConfig']['ReadonlyRootfs'] and details['Config']['User'] == '101:101', 'Web runtime isolation changed'
            assert details['HostConfig']['LogConfig']['Type'] == 'json-file', 'Unexpected log driver'
            assert request('/')[0] == 200 and request('/', auth=False)[0] == 401, 'Real TLS/Basic web startup failed'
            assert request('/api/auth/google/callback?code=closed-fixture&state=closed-fixture')[0] == 403, 'Production callback gate changed'
            compose('exec', '-T', 'web', '/usr/local/bin/sage-nginx-entrypoint', '-t', private_output=True)
            compose('exec', '-T', 'web', '/usr/local/bin/sage-nginx-entrypoint', '-s', 'reload', private_output=True)
            assert request('/')[0] == 200, 'Compose web reload failed'
            compose('restart', 'web', timeout=30)
            compose('up', '-d', '--no-deps', '--wait', 'web', timeout=90)
            assert request('/')[0] == 200, 'Compose web restart failed'
            passed('protected-web-compose-tls-healthcheck-reload-restart')
            return 0
        started = True
        compose('up', '-d', '--wait', 'db', timeout=180)
        compose('run', '--rm', 'migrate', 'python', 'manage.py', 'check', '--deploy', '--fail-level', 'ERROR')
        compose('run', '--rm', 'migrate', 'python', 'manage.py', 'makemigrations', '--check', '--dry-run')
        compose('run', '--rm', 'migrate', 'python', 'manage.py', 'migrate', '--plan')
        compose('run', '--rm', 'migrate', 'python', 'manage.py', 'migrate', '--noinput')
        compose('up', '-d', '--wait', 'app', 'web', timeout=180)
        for service in ('db', 'app', 'web'):
            containers[service] = compose('ps', '-q', service).stdout.strip()
            assert containers[service], 'Missing long-running service'
        running = compose('ps', '--services', '--status', 'running').stdout.split()
        assert set(running) == {'app', 'web', 'db'}, 'Unexpected long-running Compose service'
        passed('production-migration-and-health')

        template = ('{"user":{{json .Config.User}},"readonly":{{json .HostConfig.ReadonlyRootfs}},'
                    '"ports":{{json .NetworkSettings.Ports}},"privileged":{{json .HostConfig.Privileged}},'
                    '"caps":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},'
                    '"networks":{{json .NetworkSettings.Networks}},"mounts":{{json .Mounts}},'
                    '"image":{{json .Config.Image}},"hosts":{{json .HostConfig.ExtraHosts}}}')
        inspected = {}
        for service, uid in (('db', '999'), ('app', '10001'), ('web', '101')):
            item = json.loads(run(['docker', 'inspect', '--format', template, containers[service]]).stdout)
            inspected[service] = item
            architecture = run(['docker', 'image', 'inspect', '--platform', 'linux/amd64', '--format', '{{.Os}}/{{.Architecture}}', item['image']]).stdout.strip()
            assert architecture == 'linux/amd64', 'Unexpected image platform'
            assert run(['docker', 'exec', containers[service], 'uname', '-m']).stdout.strip() == 'x86_64', 'Unexpected executing container architecture'
            assert item['user'].split(':')[0] == uid, 'Unexpected container UID'
            assert not item['privileged'], 'Privileged container'
            assert any('no-new-privileges' in value for value in item['security']), 'Missing no-new-privileges'
            assert 'ALL' in item['caps'], 'Capabilities not fully dropped'
            assert not any(mount['Destination'] == '/var/run/docker.sock' for mount in item['mounts']), 'Docker socket mounted'
            expected_networks = {'db': {'backend'}, 'app': {'backend','frontend'}, 'web': {'frontend','edge'}}
            assert set(item['networks']) == {project + '_' + name for name in expected_networks[service]}, 'Unexpected service network attachment'
            if service in ('app', 'web'):
                assert item['readonly'], 'Writable application or gateway root filesystem'
            bindings = [binding for values in item['ports'].values() if values for binding in values]
            if service == 'web':
                assert bindings == [{'HostIp': '127.0.0.1', 'HostPort': str(port)}], 'Unexpected gateway port binding'
                assert item['hosts'] == ['app:' + app_ip], 'Gateway upstream is not pinned to the fixed app address'
                assert gateway_ip in [network['IPAddress'] for network in item['networks'].values()], 'Gateway IP drift'
            else:
                assert not bindings, 'Application or PostgreSQL host port exposed'
            if service == 'app':
                assert app_ip in [network['IPAddress'] for network in item['networks'].values()], 'App IP drift'
                destinations = [mount['Destination'] for mount in item['mounts']]
                secret_names = {Path(mount['Source']).name for mount in item['mounts'] if mount['Type'] == 'bind'}
                assert '/app/media' in destinations, 'Private avatar volume absent'
                assert not any('migrate' in destination for destination in destinations), 'Runtime has migration secret'
                assert 'db_runtime_password' in secret_names and not secret_names.intersection({'db_migrate_password','db_admin_password'}), 'Runtime receives incorrect database credential files'
        for network, internal in (('frontend', True), ('backend', True), ('edge', False)):
            state = run(['docker', 'network', 'inspect', '--format', '{{.Internal}}', project + '_' + network]).stdout.strip()
            assert state == str(internal).lower(), 'Unexpected internal/edge network isolation'
        app_shell("from django.conf import settings; import os; assert os.getuid()==10001; assert not settings.LOCAL; assert not settings.AVATAR_LOCAL_FLOCK_VERIFIED; assert settings.TRUSTED_PROXY_IPS==(" + repr(gateway_ip) + ",); assert not any((settings.AUTH_EMAIL_ENABLED,settings.AUTH_PHONE_ENABLED,settings.AUTH_GOOGLE_ENABLED,settings.AUTH_REGISTRATION_ENABLED,settings.AUTH_CAPTCHA_ENABLED,settings.ACCOUNT_DELETION_ENABLED,settings.BILLING_SALES_ENABLED,settings.BILLING_CREDITS_PURCHASE_ENABLED)); print('PRODUCTION_GATES_AND_PROXY_OK')")
        passed('isolation-and-disabled-real-functions')

        missing_secrets = run(['docker', 'run', '--rm', '--network', 'none', inspected['app']['image'],
                               'python', 'manage.py', 'check'], check=False)
        assert missing_secrets.returncode, 'Application accepts missing mounted secrets'
        assert 'FileNotFoundError' in missing_secrets.stderr and '/run/secrets/django_key' in missing_secrets.stderr, 'Missing-secret rejection has unexpected cause'
        for override, expected_error in (('SAGE_ALLOWED_HOSTS=', 'Explicit SAGE_ALLOWED_HOSTS are required'),
                                         ('SAGE_DB_NAME=', 'Production requires PostgreSQL configuration')):
            result = compose('run', '--rm', '--no-deps', '-e', override, 'migrate', 'python', 'manage.py', 'check', check=False)
            assert result.returncode, 'Missing required production input accepted'
            assert expected_error in result.stderr, 'Missing-input rejection has unexpected cause'
        passed('missing-inputs-fail-closed')

        app_shell(r'''
from django.db import connection, DatabaseError
with connection.cursor() as cursor:
    cursor.execute("SELECT current_user,current_database(),rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user")
    assert cursor.fetchone()==('sage_runtime','sage_preview',False,False,False,False)
    cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")
    assert cursor.fetchone()==('sage_migrate',)
    cursor.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolinherit FROM pg_roles WHERE rolname='sage_migrate'")
    assert cursor.fetchone()==(False,False,False,False,False)
    cursor.execute("SELECT pg_has_role(current_user,'sage_migrate','MEMBER'),pg_has_role(current_user,'postgres','MEMBER')")
    assert cursor.fetchone()==(False,False)
for statement in ("CREATE TABLE public.audit_forbidden(id int)","CREATE SCHEMA audit_forbidden","CREATE TEMP TABLE audit_forbidden(id int)","CREATE ROLE audit_forbidden","CREATE DATABASE audit_forbidden","SET ROLE sage_migrate","SET ROLE postgres"):
    try:
        with connection.cursor() as cursor: cursor.execute(statement)
    except DatabaseError as exc:
        assert getattr(exc.__cause__,'sqlstate',None)=='42501'
    else: raise AssertionError('Runtime privilege boundary failed')
print('RUNTIME_NO_DDL_OK')
''')
        app_shell("from django.db import connection; c=connection.cursor(); c.execute('CREATE TABLE public.audit_default_permissions(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, value text)'); print('DEFAULT_ACL_PROBE_CREATED')", service='migrate')
        app_shell("from django.db import connection; c=connection.cursor(); c.execute(\"INSERT INTO public.audit_default_permissions(value) VALUES ('synthetic') RETURNING id\"); pk=c.fetchone()[0]; c.execute('SELECT value FROM public.audit_default_permissions WHERE id=%s',[pk]); assert c.fetchone()==('synthetic',); c.execute(\"UPDATE public.audit_default_permissions SET value='updated' WHERE id=%s\",[pk]); c.execute(\"SELECT nextval(pg_get_serial_sequence('public.audit_default_permissions','id'))\"); assert c.fetchone()[0]>pk; c.execute('DELETE FROM public.audit_default_permissions WHERE id=%s',[pk]); assert c.rowcount==1; print('DEFAULT_ACL_DML_AND_SEQUENCE_OK')")
        app_shell("from django.db import connection; c=connection.cursor(); c.execute('DROP TABLE public.audit_default_permissions'); print('DEFAULT_ACL_PROBE_REMOVED')", service='migrate')
        passed('runtime-role-and-owner-default-permissions')

        assert request('/index.html', auth=False)[0] == 401, 'Missing gateway Basic authentication accepted'
        assert request('/index.html', headers={'Authorization': 'Basic ' + base64.b64encode(b'audit:wrong').decode()})[0] == 401, 'Incorrect gateway Basic authentication accepted'
        for path in ('/index.html', '/index-en.html', '/health/live', '/health/ready'):
            assert request(path)[0] == 200, 'Expected authenticated HTTPS endpoint unavailable'
        homepage = request('/index.html')[2]
        compressed_status, compressed_headers, compressed_homepage = request('/index.html', headers={'Accept-Encoding': 'gzip'})
        compressed_headers = {key.lower(): value for key, value in compressed_headers}
        assert compressed_status == 200 and compressed_headers.get('content-encoding') == 'gzip', 'Static HTML compression absent'
        assert gzip.decompress(compressed_homepage) == homepage, 'Compressed homepage content changed'
        assert 'accept-encoding' in compressed_headers.get('vary', '').lower(), 'Compression Vary absent'
        assert compressed_headers.get('cache-control') == 'no-cache', 'Unversioned HTML cache policy changed'
        static_status, static_headers, _ = request('/index.html')
        static_headers = {key.lower(): value for key, value in static_headers}
        assert static_status == 200 and static_headers.get('cache-control') == 'no-cache' and static_headers.get('etag'), 'Static cache revalidation absent'
        assert request('/index.html', headers={'If-None-Match': static_headers['etag']})[0] == 304, 'Static ETag revalidation failed'
        passed('static-gzip-content-identity-vary-and-etag-revalidation')
        image_homepage = run(['docker', 'exec', containers['web'], 'cat', '/srv/sage/dist/index.html'], binary=True).stdout
        homepage_sha256 = hashlib.sha256(homepage).hexdigest()
        assert homepage_sha256 == hashlib.sha256(image_homepage).hexdigest(), 'HTTPS homepage differs from freshly built web image'
        assert b'<title>Sage' in homepage and b'<body' in homepage, 'Sage homepage identity absent'
        assert b'color-scheme:dark' in homepage and b'<meta name="theme-color" content="#0b1521">' in homepage and b'data-theme="dark"' in homepage, 'Approved dark homepage markers absent'
        for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            forced_tls = ssl.create_default_context(cafile=str(directory / 'ca.crt'))
            forced_tls.minimum_version = forced_tls.maximum_version = version
            assert request('/health/live', tls_context=forced_tls)[0] == 200, 'Required TLS protocol unavailable'
        for path in ('/.env', '/.git/config', '/media/avatars/synthetic.jpg', '/app/media/avatars/synthetic.jpg'):
            assert request(path)[0] in (403, 404), 'Private build or media path accessible'
        for bad_host in ('unapproved.invalid', 'sagegogo.com.evil.invalid'):
            try:
                status = request('/index.html', host=bad_host)[0]
            except (http.client.RemoteDisconnected, ConnectionResetError):
                status = 444
            assert status in (400, 403, 404, 421, 444), 'Unexpected Host accepted'
        try:
            request('/index.html', sni='unapproved.invalid')
        except (ssl.SSLError, ConnectionResetError):
            pass
        else:
            raise AssertionError('Unexpected SNI accepted')
        passed('https-basic-auth-host-sni-and-private-paths')

        status, response_headers, body = request('/api/auth/session', headers={'Accept-Encoding': 'gzip'})
        assert status == 200, 'Anonymous session unavailable'
        session_headers = {key.lower(): value for key, value in response_headers}
        assert 'content-encoding' not in session_headers, 'Private API response compressed'
        assert session_headers.get('cache-control') == 'no-store', 'Private API response permits caching'
        session = json.loads(body)
        assert session['authenticated'] is False and session['user'] is None, 'Unexpected authenticated fixture'
        assert all(not session['capabilities'][key] for key in ('email_login','phone_login','password_login','password_reset','email_registration','phone_registration','google_login','google_registration','captcha')), 'Real authentication capability enabled'
        cookies = SimpleCookie()
        for key, value in response_headers:
            if key.lower() == 'set-cookie':
                cookies.load(value)
        for name in ('sessionid', 'csrftoken'):
            assert name in cookies, 'Expected secure cookie absent'
            assert cookies[name]['secure'] and cookies[name]['httponly'] and cookies[name]['samesite'].lower() == 'lax', 'Cookie security flags missing'
        cookie_header = '; '.join(f'{name}={value.value}' for name, value in cookies.items())
        redactions.extend([value.value for value in cookies.values()] + [session['csrf_token']])
        headers = {'Cookie': cookie_header, 'Content-Type': 'application/json',
                   'X-CSRFToken': session['csrf_token'], 'Origin': 'https://sagegogo.com'}
        assert request('/api/auth/logout', method='POST', body='{}', headers={'Cookie': cookie_header, 'Content-Type': 'application/json'})[0] == 403, 'Missing CSRF accepted'
        assert request('/api/auth/cancel', method='POST', body='{}', headers=headers)[0] in (200, 400, 422), 'Valid CSRF rejected'
        consultation = json.dumps({'company': 'Synthetic audit', 'email': 'audit@example.invalid',
                                   'phone': '+8613800000000', 'question': 'Synthetic restricted preview check'})
        status = request('/api/consultations', method='POST', body=consultation,
                         headers={**headers, 'Idempotency-Key': 'synthetic-docker-audit'})[0]
        assert status == 403, 'Preview-disabled consultation was not blocked by gateway'
        assert request('/api/auth/google/callback?code=SYNTHETIC_CALLBACK_MARKER&state=SYNTHETIC_CALLBACK_MARKER', headers=headers)[0] == 403, 'Preview-disabled OAuth callback was not blocked by gateway'
        assert request('/api/account/avatar-image', headers={'Cookie': cookie_header})[0] == 401, 'Anonymous private avatar accessible'
        app_shell("from django.contrib.auth import get_user_model; from django.contrib.sessions.models import Session; from backend.models import Consultation,ConsultationAudit; from backend.commerce.models import Order,PaymentFact; assert get_user_model().objects.count()==0; assert Consultation.objects.count()==0; assert ConsultationAudit.objects.count()==0; assert Order.objects.count()==0; assert PaymentFact.objects.count()==0; assert Session.objects.count()==1; print('ONLY_ANONYMOUS_SESSION_WRITE_OK')")
        passed('anonymous-session-csrf-and-preview-write-gates')

        compose('run', '--rm', '--no-deps', 'migrate', 'python', '-c', r'''
import http.client
connection=http.client.HTTPConnection('app',8000,timeout=5)
connection.request('GET','/health/live',headers={'Host':'sagegogo.com','X-Forwarded-Proto':'https','X-Real-IP':'192.0.2.123','X-Forwarded-For':'192.0.2.123'})
response=connection.getresponse(); assert response.status==301; assert response.getheader('Location').startswith('https://sagegogo.com/'); connection.close()
print('UNTRUSTED_PROTO_REAL_TOPOLOGY_OK')
''')
        app_shell(r'''
from django.test import RequestFactory
from backend.http import client_ip
from django.conf import settings
factory=RequestFactory()
assert client_ip(factory.get('/',REMOTE_ADDR='127.0.0.1',HTTP_X_REAL_IP='192.0.2.123'))=='127.0.0.1'
assert client_ip(factory.get('/',REMOTE_ADDR=settings.TRUSTED_PROXY_IPS[0],HTTP_X_REAL_IP='192.0.2.123'))=='192.0.2.123'
assert client_ip(factory.get('/',REMOTE_ADDR=settings.TRUSTED_PROXY_IPS[0],HTTP_X_REAL_IP='192.0.2.123,192.0.2.124'))==settings.TRUSTED_PROXY_IPS[0]
print('UNTRUSTED_PROTO_AND_SINGLE_IP_OK')
''')
        status = request('/health/live', headers={'X-Forwarded-Proto':'http','X-Real-IP':'192.0.2.123','X-Forwarded-For':'192.0.2.123'})[0]
        assert status == 200, 'Client-supplied forwarding headers override real HTTPS'
        passed('precise-proxy-real-topology')

        # This exercises the real mounted filesystem, without creating any account.
        flock_code = r'''
import os, subprocess, sys, uuid
from pathlib import Path
from django.conf import settings
from django.test import override_settings
from backend.account import _avatar_namespace_lock, _avatar_storage_ready
assert str(settings.MEDIA_ROOT)=='/app/media'
assert not settings.AVATAR_LOCAL_FLOCK_VERIFIED
try: _avatar_storage_ready()
except OSError: pass
else: raise AssertionError('Avatar default gate missing')
token='avatars/'+uuid.uuid4().hex+'/'
with override_settings(AVATAR_LOCAL_FLOCK_VERIFIED=True):
    with _avatar_namespace_lock(token,create=True):
        marker=Path(settings.MEDIA_ROOT)/token/'.lock'; inode=marker.stat().st_ino
        code="import fcntl,sys; f=open(sys.argv[1],'r+b'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        child=subprocess.run([sys.executable,'-B','-c',code,str(marker)],capture_output=True)
        assert child.returncode!=0 and b'BlockingIOError' in child.stderr
    subprocess.run([sys.executable,'-B','-c',code,str(marker)],check=True,capture_output=True)
    child=subprocess.Popen([sys.executable,'-B','-u','-c',"import fcntl,os,sys,time; f=open(sys.argv[1],'r+b'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); print('LOCKED',flush=True); time.sleep(30)",str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert child.stdout.readline()==b'LOCKED\n'
        with open(marker,'r+b') as file:
            import fcntl
            try: fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: pass
            else: raise AssertionError('Independent holder did not lock')
    finally:
        child.kill(); child.communicate(timeout=5)
    with _avatar_namespace_lock(token): pass
    assert marker.stat().st_ino==inode
assert not settings.AVATAR_LOCAL_FLOCK_VERIFIED
print('ACTUAL_MEDIA_VOLUME_FLOCK_OK')
'''
        app_shell(flock_code)
        passed('actual-media-volume-independent-process-flock')

        # Reuse the real transaction/failure tests against a separate synthetic DB
        # and this audit's actual avatar volume. Never enable the running app gate.
        psql(containers['db'], 'CREATE DATABASE sage_avatar_audit OWNER sage_migrate TEMPLATE template0;')
        avatar_arguments = ['run', '--rm', '--no-deps', '-e', 'SAGE_ENV=test',
                            '-e', 'DJANGO_SETTINGS_MODULE=backend.settings',
                            '-e', 'SAGE_DB_NAME=sage_avatar_audit', '-e', 'SAGE_MEDIA_ROOT=/app/media',
                            '--volume', project + '_avatars:/app/media', 'migrate']
        try:
            compose(*avatar_arguments, 'python', 'manage.py', 'migrate', '--noinput')
            compose(*avatar_arguments, 'python', '-B', '-c', r'''
import django, tempfile
from pathlib import Path
django.setup()
from django.conf import settings
from django.db import connections
from django.test.runner import DiscoverRunner
assert settings.LOCAL and settings.DATABASES['default']['NAME']=='sage_avatar_audit'
assert str(settings.MEDIA_ROOT)=='/app/media' and not settings.AVATAR_LOCAL_FLOCK_VERIFIED
tempfile.tempdir='/app/media'
from backend.account_tests import AccountCentreTests
labels=['backend.account_tests.AvatarTransactionTests']
labels += ['backend.account_tests.AccountCentreTests.'+name for name in dir(AccountCentreTests)
           if name.startswith('test_') and ('avatar' in name or 'cleanup' in name)]
runner=DiscoverRunner(verbosity=2)
runner.setup_test_environment()
try:
    result=runner.run_suite(runner.build_suite(labels))
    assert result.wasSuccessful() and not result.skipped
    assert not list(Path('/app/media').glob('sage-avatar-*'))
finally:
    connections.close_all()
    runner.teardown_test_environment()
print('MOUNTED_VOLUME_AVATAR_FAULT_MATRIX_OK')
''', timeout=240)
        finally:
            psql(containers['db'], 'DROP DATABASE sage_avatar_audit WITH (FORCE);')
        app_shell('from django.conf import settings; assert not settings.AVATAR_LOCAL_FLOCK_VERIFIED; print("AVATAR_RUNNING_APP_GATE_STILL_CLOSED")')
        passed('mounted-volume-avatar-upload-cleanup-complete-fault-matrix')

        before_restart = fingerprint(containers['db'])
        compose('restart', 'db', 'app', 'web', timeout=120)
        last_readiness = None
        for _ in range(60):
            try:
                last_readiness = request('/health/ready')[0]
                if last_readiness == 200:
                    break
            except (OSError, http.client.HTTPException) as exc:
                last_readiness = type(exc).__name__
            time.sleep(1)
        else:
            raise AssertionError(f'Readiness did not recover after restart; last result: {last_readiness}')
        assert fingerprint(containers['db']) == before_restart, 'Rows, sequences or ACL changed after restart'
        assert json.loads(request('/api/auth/session', headers={'Cookie': cookie_header})[2])['authenticated'] is False, 'Session lost after restart'
        app_shell("from django.contrib.sessions.models import Session; assert Session.objects.count()==1; print('SESSION_PERSISTED_WITHOUT_REPLACEMENT')")
        compose('stop', 'db', timeout=30)
        assert request('/health/live')[0] == 200, 'Liveness incorrectly depends on DB'
        assert request('/health/ready')[0] == 503, 'Readiness falsely succeeds with unavailable DB'
        compose('up', '-d', '--wait', 'db', timeout=120)
        assert request('/health/ready')[0] == 200, 'Readiness did not recover after DB fault'
        passed('persistence-and-database-fault-recovery')

        source_fingerprint = fingerprint(containers['db'])
        archive = run(['docker', 'exec', containers['db'], 'pg_dump', '-U', 'postgres', '-Fc', 'sage_preview'], binary=True).stdout
        archive_path = directory / 'synthetic-preview.dump'
        archive_path.write_bytes(archive)
        archive_path.chmod(0o600)
        run(['docker', 'volume', 'create', '--label', 'sage.audit.project=' + project, restore_volume])
        restore_volume_created = True
        run(['docker', 'run', '-d', '--name', restore_container, '--network', 'none',
             '--label', 'sage.audit.project=' + project,
             '--mount', f'type=volume,src={restore_volume},dst=/var/lib/postgresql/data',
             '--mount', f'type=bind,src={secret_dir / "db_admin_password"},dst=/run/secrets/db_admin_password,readonly',
             '-e', 'POSTGRES_PASSWORD_FILE=/run/secrets/db_admin_password',
             inspected['db']['image']])
        restore_started = True
        for _ in range(60):
            result = run(['docker', 'exec', restore_container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], check=False)
            if not result.returncode:
                break
            time.sleep(1)
        else:
            raise AssertionError('Independent restore PostgreSQL did not become ready')
        run(['docker', 'exec', '-i', restore_container, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres'], data='CREATE ROLE sage_migrate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; CREATE ROLE sage_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; CREATE DATABASE sage_preview OWNER sage_migrate TEMPLATE template0; REVOKE ALL ON DATABASE sage_preview FROM PUBLIC; GRANT CONNECT ON DATABASE sage_preview TO sage_runtime;')
        run(['docker', 'exec', '-i', restore_container, 'pg_restore', '-U', 'postgres', '--single-transaction', '--exit-on-error', '-d', 'sage_preview'], data=archive, binary=True)
        assert fingerprint(restore_container) == source_fingerprint, 'Restored rows, sequences, schema, owners, ACL or roles differ'
        psql(restore_container, "SET ROLE sage_migrate; CREATE TABLE public.audit_restored_defaults(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,value text); RESET ROLE; SET ROLE sage_runtime; INSERT INTO public.audit_restored_defaults(value) VALUES ('synthetic'); UPDATE public.audit_restored_defaults SET value='updated'; SELECT value FROM public.audit_restored_defaults; DELETE FROM public.audit_restored_defaults; RESET ROLE; SET ROLE sage_migrate; DROP TABLE public.audit_restored_defaults;")
        for statement in ('CREATE TABLE public.audit_forbidden(id int)', 'CREATE TEMP TABLE audit_forbidden(id int)', 'CREATE SCHEMA audit_forbidden', 'SET ROLE sage_migrate'):
            result = run(['docker', 'exec', '-i', restore_container, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'sage_runtime', '-d', 'sage_preview'], data=statement, check=False)
            assert result.returncode, 'Restored runtime privilege boundary failed'
        assert fingerprint(restore_container) == source_fingerprint, 'Restore permissions probe left unexpected mutations'
        psql(restore_container, "UPDATE django_session SET expire_date=expire_date+interval '1 day';")
        assert fingerprint(restore_container) != source_fingerprint, 'Restore comparator missed corrupted synthetic row'
        passed('independent-volume-restore-schema-owner-acl-defaults-and-negative-control')

        logs = compose('logs', '--no-color', 'app', 'web', 'db').stdout
        assert 'SYNTHETIC_CALLBACK_MARKER' not in logs, 'OAuth query marker leaked to container logs'
        passed('blocked-oauth-query-log-safety')
    except (Exception, KeyboardInterrupt) as exc:
        failure = redact(str(exc) or type(exc).__name__)
        if restore_started:
            try:
                run(['docker', 'logs', restore_container], check=False)
            except Exception:
                pass
        if started:
            try:
                compose('logs', '--no-color', 'app', 'web', 'db', check=False)
            except Exception:
                pass
    finally:
        cleanup_failures = []
        if log_probe_started:
            try:
                run(['docker', 'rm', '-f', log_probe_container])
            except Exception:
                cleanup_failures.append('log-probe-container')
        if restore_started:
            try:
                run(['docker', 'rm', '-f', restore_container])
            except Exception:
                cleanup_failures.append('restore-container')
        if restore_volume_created:
            try:
                run(['docker', 'volume', 'rm', restore_volume])
            except Exception:
                cleanup_failures.append('restore-volume')
        if started:
            try:
                compose('down', '--volumes', '--remove-orphans', '--timeout', '10', timeout=120)
            except Exception:
                cleanup_failures.append('compose-project')
        if cleanup_failures:
            failure = failure or 'Audit resource cleanup failed'
        # Keys and cookies are never retained with diagnostic material.
        shutil.rmtree(secret_dir)
        (directory / 'ca.key').unlink(missing_ok=True)
        archive_path = directory / 'synthetic-preview.dump'
        archive_path.unlink(missing_ok=True)
        open_findings = (['nginx-error-log-query-privacy']
                         if log_privacy and not all(log_privacy.values()) else [])
        summary = {'status': 'failed' if failure else 'passed-with-findings' if open_findings else 'passed', 'project': project,
                   'checks': checked, 'failure': failure, 'cleanup_failures': cleanup_failures,
                   'homepage_sha256': homepage_sha256,
                   'log_query_absent': log_privacy,
                   'nginx_log_supervision': log_supervision,
                   'open_findings': open_findings,
                   'evidence': str(directory), 'scope': 'local synthetic Linux/amd64 containers; no provider call',
                   'not_covered': ['real providers and cloud deployment',
                                   'cloud OAuth error-log deployment', 'restored role password authentication',
                                   'off-host or point-in-time recovery']}
        (directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if failure else 0


if __name__ == '__main__':
    sys.exit(main())
