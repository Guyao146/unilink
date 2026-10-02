import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import app as srv
from runtime import Runtime
from store import Store, PENDING
from test_flow import make_cfg, FAKE_USER


class RuntimeConfigTest(unittest.TestCase):
    def test_limits_must_be_positive(self):
        for name in ('UNILINK_HTTP_LIMIT', 'UNILINK_GC_INTERVAL'):
            for value in ('0', '-1', 'invalid'):
                with self.subTest(name=name, value=value):
                    with patch.dict(os.environ, {name: value}):
                        with self.assertRaises(ValueError):
                            Runtime()

    def test_empty_limits_use_defaults(self):
        with patch.dict(os.environ, {'UNILINK_HTTP_LIMIT': '', 'UNILINK_GC_INTERVAL': ''}):
            rt = Runtime()
        self.assertEqual((rt.limit, rt.gc_interval), (16, 20))


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.connections = set()
        self.slow_body = False
        upstream = web.Application(handler_args={'handler_cancellation': True})

        async def userinfo(request):
            self.calls += 1
            self.connections.add(request.transport)
            if self.slow_body:
                response = web.StreamResponse(headers={'Content-Type': 'application/json'})
                await response.prepare(request)
                await response.write(b'{')
                self.entered.set()
                await self.release.wait()
                await response.write(json.dumps(FAKE_USER).encode()[1:])
                return response
            self.entered.set()
            await self.release.wait()
            return web.json_response(FAKE_USER)

        upstream.router.add_get('/application/o/userinfo/', userinfo)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.addAsyncCleanup(self.upstream.close)
        cfg = make_cfg('http://127.0.0.1', str(self.upstream.make_url('')).rstrip('/'))
        self.app = srv.build_app(cfg, os.path.join(self.tmp.name, 'key.pem'))
        self.runtime = self.app['runtime']
        self.runtime.gc_interval = 0.02
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def ticket(self):
        return self.app['store'].create(client_id='unilink-qr').ticket

    async def approve(self, ticket):
        return await self.client.post('/api/scan/approve', json={
            'ticket': ticket, 'access_token': 'valid'})

    async def test_duplicate_and_overload_rejected_without_upstream_queue(self):
        ticket = self.ticket()
        first = asyncio.create_task(self.approve(ticket))
        try:
            await asyncio.wait_for(self.entered.wait(), 3)
            r = await self.approve(ticket)
            self.assertEqual(r.status, 409)
            await r.read()
            self.runtime.limit = 1
            r = await self.approve(self.ticket())
            self.assertEqual(r.status, 503)
            self.assertEqual(r.headers['Retry-After'], '2')
            await r.read()
            self.assertEqual(self.calls, 1)
            self.release.set()
            r = await first
            self.assertEqual(r.status, 200)
            await r.read()
            self.assertEqual(self.runtime.active, {})
        finally:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)

    async def test_disconnect_cancels_upstream_and_releases_slot(self):
        ticket = self.ticket()
        url = self.client.make_url('/')
        reader, writer = await asyncio.open_connection(url.host, url.port)
        body = json.dumps({'ticket': ticket, 'access_token': 'valid'}).encode()
        writer.write(('POST /api/scan/approve HTTP/1.1\r\nHost: localhost\r\n'
                      'Content-Type: application/json\r\nContent-Length: %d\r\n\r\n'
                      % len(body)).encode() + body)
        await writer.drain()
        try:
            await asyncio.wait_for(self.entered.wait(), 3)
        finally:
            writer.close()
            await writer.wait_closed()
        await until(lambda: not self.runtime.active)
        self.assertEqual(self.app['store'].get(ticket).state, PENDING)
        self.assertEqual(len(self.app['http'].connector._acquired), 0)
        self.release.set()
        r = await self.approve(ticket)
        self.assertEqual(r.status, 200)
        await r.read()

    async def test_shutdown_cancels_requests_closes_pool_and_gc(self):
        task = asyncio.create_task(self.approve(self.ticket()))
        await asyncio.wait_for(self.entered.wait(), 3)
        http = self.app['http']
        pool = http.connector
        try:
            await asyncio.wait_for(self.client.close(), 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(http.closed)
        self.assertTrue(pool.closed)
        self.assertTrue(self.runtime.sweeper.done())
        self.assertEqual(self.runtime.active, {})
        self.assertEqual(self.app['store'].stats()['sessions'], 0)

    async def test_timer_collects_current_store_after_hot_reload(self):
        replacement = Store(1, 1, 1)
        s = replacement.create()
        s.expires = 0
        self.app['store'] = replacement
        await until(lambda: replacement.stats()['sessions'] == 0)
        self.assertFalse(self.runtime.sweeper.done())

    async def test_timeout_is_504_and_slot_reusable(self):
        ticket = self.ticket()
        with patch('identity.aiohttp.ClientTimeout', return_value=aiohttp.ClientTimeout(total=0.05)):
            r = await self.approve(ticket)
        self.assertEqual(r.status, 504)
        await r.read()
        self.assertEqual(self.runtime.active, {})
        self.assertEqual(len(self.app['http'].connector._acquired), 0)
        self.release.set()
        r = await self.approve(ticket)
        self.assertEqual(r.status, 200)
        await r.read()

    async def test_deny_cancels_body_read_without_affecting_other_login(self):
        self.slow_body = True
        first_ticket, other_ticket = self.ticket(), self.ticket()
        first = asyncio.create_task(self.approve(first_ticket))
        other = asyncio.create_task(self.approve(other_ticket))
        try:
            await until(lambda: self.calls == 2)
            r = await self.client.post('/api/scan/deny', json={'ticket': first_ticket})
            self.assertEqual(r.status, 200)
            await r.read()
            await until(lambda: first_ticket not in self.runtime.active)
            self.assertIn(other_ticket, self.runtime.active)
            self.assertEqual(len(self.app['http'].connector._acquired), 1)
            self.release.set()
            r = await other
            self.assertEqual(r.status, 200)
            await r.read()
            self.assertEqual(self.runtime.active, {})
        finally:
            first.cancel()
            other.cancel()
            await asyncio.gather(first, other, return_exceptions=True)

    async def test_repeated_logins_reuse_pool_without_pending_work(self):
        self.release.set()
        http = self.app['http']
        for _ in range(20):
            r = await self.approve(self.ticket())
            self.assertEqual(r.status, 200)
            await r.read()
            self.assertIs(self.app['http'], http)
            self.assertEqual(self.runtime.active, {})
            self.assertEqual(len(http.connector._acquired), 0)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(http.connector.limit, self.runtime.limit)
        self.assertIsInstance(http.cookie_jar, aiohttp.DummyCookieJar)

    async def test_reload_cancels_old_work_and_clears_old_store(self):
        old_store = self.app['store']
        task = asyncio.create_task(self.approve(self.ticket()))
        try:
            await asyncio.wait_for(self.entered.wait(), 3)
            with patch('app.config.load_or_none', return_value=self.app['cfg']):
                self.assertTrue(srv._reload_config(self.app))
            await until(lambda: not self.runtime.active)
            self.assertEqual(old_store.stats()['sessions'], 0)
            self.assertIsNot(old_store, self.app['store'])
            self.assertFalse(self.app['http'].closed)
            self.release.set()
            r = await self.approve(self.ticket())
            self.assertEqual(r.status, 200)
            await r.read()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_setup_mode_also_closes_process_resources(self):
        app = srv.build_app(None)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get('/healthz')
            self.assertEqual((await r.json())['status'], 'setup_required')
            self.assertFalse(app['runtime'].sweeper.done())
        finally:
            await client.close()
        self.assertTrue(app['http'].closed)
        self.assertTrue(app['runtime'].sweeper.done())

    async def test_denied_session_does_not_call_upstream(self):
        ticket = self.ticket()
        self.app['store'].deny(ticket)
        r = await self.approve(ticket)
        self.assertEqual(r.status, 409)
        await r.read()
        self.assertEqual(self.calls, 0)
