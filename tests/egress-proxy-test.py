#!/usr/bin/env python3
"""Local, network-free checks for drafts/restricted-deploy/egress_proxy.py."""
import asyncio
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'egress_proxy', Path(__file__).resolve().parents[1] / 'drafts/restricted-deploy/egress_proxy.py')
egress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(egress)


async def echo_server():
    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(b'echo:' + data)
            await writer.drain()
        writer.close()
    server = await asyncio.start_server(echo, '127.0.0.1', 0)
    return server, server.sockets[0].getsockname()[1]


class EgressProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.echo, self.echo_port = await echo_server()
        self.addAsyncCleanup(self._close, self.echo)

    async def _close(self, server):
        server.close()
        await server.wait_closed()

    async def proxy(self, policy, resolver=None):
        async def loopback_as_public(host, port):
            return [(None, '127.0.0.1')]
        server = await egress.serve(policy, '127.0.0.1', 0, resolver or loopback_as_public)
        self.addAsyncCleanup(self._close, server)
        return server.sockets[0].getsockname()[1]

    async def request(self, port, head):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(head)
        await writer.drain()
        status = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
        return status.split(b'\r\n')[0], reader, writer

    def policy(self, **kwargs):
        return egress.Policy(ports=frozenset({self.echo_port}), **kwargs)

    async def test_allowed_host_tunnels_bytes(self):
        port = await self.proxy(self.policy(allow=frozenset({'ses.tencentcloudapi.com'})))
        status, reader, writer = await self.request(
            port, f'CONNECT ses.tencentcloudapi.com:{self.echo_port} HTTP/1.1\r\n\r\n'.encode())
        self.assertEqual(status, b'HTTP/1.1 200 Connection Established')
        writer.write(b'tls-bytes')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(64), 5), b'echo:tls-bytes')
        writer.close()

    async def test_unlisted_host_wrong_port_and_plain_http_are_refused(self):
        port = await self.proxy(self.policy(allow=frozenset({'ses.tencentcloudapi.com'})))
        for head, expected in (
                (f'CONNECT example.com:{self.echo_port} HTTP/1.1\r\n\r\n', b'403'),
                ('CONNECT ses.tencentcloudapi.com:22 HTTP/1.1\r\n\r\n', b'403'),
                ('GET http://ses.tencentcloudapi.com/ HTTP/1.1\r\n\r\n', b'405'),
                ('CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n', b'400'),
                ('garbage\r\n\r\n', b'400')):
            status, _, writer = await self.request(port, head.encode())
            self.assertIn(expected, status, head)
            writer.close()

    async def test_private_resolution_is_refused(self):
        async def private(host, port):
            return await egress.resolve_public('localhost', port)
        port = await self.proxy(self.policy(allow=frozenset({'ses.tencentcloudapi.com'})), private)
        status, _, writer = await self.request(
            port, f'CONNECT ses.tencentcloudapi.com:{self.echo_port} HTTP/1.1\r\n\r\n'.encode())
        self.assertIn(b'403', status)
        writer.close()

    async def test_upstream_hosts_chain_through_relay_or_are_refused_without_it(self):
        relay = await self.proxy(self.policy(allow=frozenset({'oauth2.googleapis.com'})))
        port = await self.proxy(self.policy(allow=frozenset({'ses.tencentcloudapi.com'}),
                                            upstream_hosts=frozenset({'oauth2.googleapis.com'}),
                                            upstream=('127.0.0.1', relay)))
        status, reader, writer = await self.request(
            port, f'CONNECT oauth2.googleapis.com:{self.echo_port} HTTP/1.1\r\n\r\n'.encode())
        self.assertEqual(status, b'HTTP/1.1 200 Connection Established')
        writer.write(b'token-exchange')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(64), 5), b'echo:token-exchange')
        writer.close()
        closed = await self.proxy(self.policy(allow=frozenset({'ses.tencentcloudapi.com'}),
                                              upstream_hosts=frozenset({'oauth2.googleapis.com'})))
        status, _, writer = await self.request(
            closed, f'CONNECT oauth2.googleapis.com:{self.echo_port} HTTP/1.1\r\n\r\n'.encode())
        self.assertIn(b'403', status)
        writer.close()

    async def test_relay_refusal_is_reported_as_forbidden(self):
        relay = await self.proxy(self.policy(allow=frozenset()))
        port = await self.proxy(self.policy(allow=frozenset(), upstream_hosts=frozenset({'www.googleapis.com'}),
                                            upstream=('127.0.0.1', relay)))
        status, _, writer = await self.request(
            port, f'CONNECT www.googleapis.com:{self.echo_port} HTTP/1.1\r\n\r\n'.encode())
        self.assertIn(b'403', status)
        writer.close()

    def test_configuration_is_strict(self):
        env = {'SAGE_EGRESS_ALLOW': 'ses.tencentcloudapi.com, SMS.tencentcloudapi.com',
               'SAGE_EGRESS_UPSTREAM_HOSTS': 'oauth2.googleapis.com', 'SAGE_EGRESS_UPSTREAM': 'relay.example:3128'}
        policy = egress.Policy.from_env(env)
        self.assertEqual(policy.allow, {'ses.tencentcloudapi.com', 'sms.tencentcloudapi.com'})
        self.assertEqual(policy.upstream, ('relay.example', 3128))
        self.assertEqual(policy.route('ses.tencentcloudapi.com', 443), 'direct')
        self.assertEqual(policy.route('oauth2.googleapis.com', 443), 'upstream')
        self.assertIsNone(policy.route('ses.tencentcloudapi.com', 80))
        for bad in ({'SAGE_EGRESS_ALLOW': '*.tencentcloudapi.com'}, {'SAGE_EGRESS_ALLOW': '10.0.0.1'},
                    {'SAGE_EGRESS_ALLOW': 'a.com', 'SAGE_EGRESS_UPSTREAM_HOSTS': 'a.com'},
                    {'SAGE_EGRESS_UPSTREAM': 'no-port'}):
            with self.assertRaises(ValueError, msg=bad):
                egress.Policy.from_env(bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
