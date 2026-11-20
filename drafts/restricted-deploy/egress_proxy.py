"""Allowlisted HTTPS egress: CONNECT tunnels only, to named hosts, never to private addresses.

The app container has no direct internet route; supplier SDKs reach this proxy through
HTTPS_PROXY. Hosts in SAGE_EGRESS_ALLOW are dialled directly. Hosts in
SAGE_EGRESS_UPSTREAM_HOSTS are tunnelled through SAGE_EGRESS_UPSTREAM (another instance of
this proxy outside mainland China, for Google), or refused when no upstream is set.
TLS stays end to end: the proxy never sees request bodies, credentials or responses.
"""
import asyncio
import ipaddress
import logging
import os
import re
import socket

# DNS names only: the last label must start with a letter, so IP literals never match.
HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
MAX_HEAD = 8192
CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 120

logger = logging.getLogger('sage.egress')


def hosts(value):
    names = {name.strip().lower() for name in (value or '').split(',') if name.strip()}
    if any(not HOST.fullmatch(name) for name in names):
        raise ValueError('Egress host lists must contain exact DNS names only')
    return frozenset(names)


class Policy:
    def __init__(self, allow, upstream_hosts=frozenset(), upstream=None, ports=frozenset({443})):
        if allow & upstream_hosts:
            raise ValueError('A host cannot be both direct and upstream')
        self.allow, self.upstream_hosts, self.upstream, self.ports = allow, upstream_hosts, upstream, ports

    @classmethod
    def from_env(cls, env=os.environ):
        upstream = env.get('SAGE_EGRESS_UPSTREAM', '').strip()
        if upstream:
            host, _, port = upstream.rpartition(':')
            if not host or not port.isdigit():
                raise ValueError('SAGE_EGRESS_UPSTREAM must be host:port')
            upstream = (host, int(port))
        return cls(hosts(env.get('SAGE_EGRESS_ALLOW')), hosts(env.get('SAGE_EGRESS_UPSTREAM_HOSTS')),
                   upstream or None)

    def route(self, host, port):
        """Return 'direct', 'upstream' or None (refused)."""
        if port not in self.ports:
            return None
        if host in self.allow:
            return 'direct'
        if host in self.upstream_hosts and self.upstream:
            return 'upstream'
        return None


async def resolve_public(host, port):
    """Resolve and keep only globally routable addresses, so DNS cannot point a tunnel inward."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = []
    for family, _, _, _, sockaddr in infos:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_global and not address.is_multicast:
            addresses.append((family, sockaddr[0]))
    return addresses


async def dial(policy, host, port, resolver=resolve_public):
    route = policy.route(host, port)
    if route == 'direct':
        addresses = await resolver(host, port)
        if not addresses:
            raise PermissionError('NO_PUBLIC_ADDRESS')
        last = None
        for _, address in addresses:
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port), CONNECT_TIMEOUT)
            except OSError as exc:
                last = exc
        raise last or OSError('UNREACHABLE')
    if route == 'upstream':
        reader, writer = await asyncio.wait_for(asyncio.open_connection(*policy.upstream), CONNECT_TIMEOUT)
        writer.write(f'CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n'.encode('ascii'))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), CONNECT_TIMEOUT)
        if not re.match(rb'HTTP/1\.[01] 200 ', head):
            writer.close()
            raise PermissionError('UPSTREAM_REFUSED')
        return reader, writer
    raise PermissionError('NOT_ALLOWED')


async def pipe(reader, writer):
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


def parse(head):
    lines = head.decode('ascii', 'strict').split('\r\n')
    method, target, version = lines[0].split(' ')
    if version not in ('HTTP/1.1', 'HTTP/1.0'):
        raise ValueError
    if method != 'CONNECT':
        return method, None, None
    host, _, port = target.rpartition(':')
    host = host.lower()
    if not HOST.fullmatch(host) or not port.isdigit():
        raise ValueError
    return method, host, int(port)


async def respond(writer, status):
    writer.write(f'HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode('ascii'))
    try:
        await writer.drain()
    finally:
        writer.close()


def handler(policy, resolver=resolve_public):
    async def handle(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), CONNECT_TIMEOUT)
            if len(head) > MAX_HEAD:
                raise ValueError
            method, host, port = parse(head)
        except (ValueError, UnicodeError, asyncio.LimitOverrunError, asyncio.IncompleteReadError,
                asyncio.TimeoutError, OSError):
            return await respond(writer, '400 Bad Request')
        if method != 'CONNECT':
            logger.info('refused method=%s', method[:16])
            return await respond(writer, '405 Method Not Allowed')
        try:
            upstream_reader, upstream_writer = await dial(policy, host, port, resolver)
        except PermissionError as exc:
            logger.info('refused host=%s port=%s reason=%s', host, port, exc)
            return await respond(writer, '403 Forbidden')
        except (OSError, asyncio.TimeoutError):
            logger.warning('unreachable host=%s port=%s', host, port)
            return await respond(writer, '502 Bad Gateway')
        logger.info('tunnel host=%s port=%s', host, port)
        writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        await writer.drain()
        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))
    return handle


async def serve(policy, host, port, resolver=resolve_public):
    return await asyncio.start_server(handler(policy, resolver), host, port, limit=MAX_HEAD)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    policy = Policy.from_env()
    listen = os.environ.get('SAGE_EGRESS_LISTEN', '0.0.0.0:3128')
    host, _, port = listen.rpartition(':')

    async def run():
        server = await serve(policy, host, int(port))
        logger.info('egress ready direct=%s upstream=%s', ','.join(sorted(policy.allow)),
                    ','.join(sorted(policy.upstream_hosts)) if policy.upstream else 'none')
        async with server:
            await server.serve_forever()
    asyncio.run(run())


if __name__ == '__main__':
    main()
