"""进程级异步资源：有限并发、定期回收和有序退出，不为请求创建线程。"""
import asyncio
from contextlib import asynccontextmanager, suppress
import os

import aiohttp
from aiohttp import web

import admin


def _positive_env(name, default):
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if value <= 0:
        raise ValueError("%s 必须大于 0" % name)
    return value


class Runtime:
    def __init__(self):
        # 进程资源项只在启动时读取，不参与面板业务配置热更新。
        self.limit = _positive_env("UNILINK_HTTP_LIMIT", 16)
        self.gc_interval = _positive_env("UNILINK_GC_INTERVAL", 20)
        self.active = {}
        self.stopping = False
        self.http = None
        self.sweeper = None

    @asynccontextmanager
    async def approval(self, ticket):
        if ticket in self.active:
            raise web.HTTPConflict(
                text='{"error":"in_progress","error_description":"该二维码正在确认中"}',
                content_type="application/json")
        if self.stopping or len(self.active) >= self.limit:
            raise web.HTTPServiceUnavailable(
                text='{"error":"busy","error_description":"服务繁忙，请稍后重试"}',
                content_type="application/json", headers={"Retry-After": "2"})
        # 单个事件循环里检查和登记之间没有 await，不会抢到同一名额。
        self.active[ticket] = asyncio.current_task()
        try:
            yield
        finally:
            self.active.pop(ticket, None)

    def cancel(self, ticket):
        task = self.active.get(ticket)
        if task is not None:
            task.cancel()

    def cancel_pending(self):
        for ticket in list(self.active):
            self.cancel(ticket)

    async def stop(self):
        self.stopping = True
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.active.clear()

    async def collect(self, app):
        while True:
            await asyncio.sleep(self.gc_interval)
            # 每次取当前 store，面板热更新后不能仍引用旧 store。
            store = app.get("store")
            if store is not None:
                store.prune()
            admin.prune_sessions()

    async def context(self, app):
        connector = aiohttp.TCPConnector(
            limit=self.limit, limit_per_host=self.limit,
            keepalive_timeout=15, ttl_dns_cache=60)
        async with aiohttp.ClientSession(
                connector=connector, cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=10, connect=3, sock_read=5)) as http:
            self.http = app["http"] = http
            self.sweeper = asyncio.create_task(self.collect(app), name="unilink-auth-gc")
            try:
                yield
            finally:
                await self.stop()
                self.sweeper.cancel()
                with suppress(asyncio.CancelledError):
                    await self.sweeper
                store = app.get("store")
                if store is not None:
                    store.clear()
                admin.prune_sessions()
        # 给 SSL transport 的关闭回调留出事件循环时间，再由 run_app 关闭 loop。
        await asyncio.sleep(0.25)
