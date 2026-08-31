# -*- coding: utf-8 -*-
"""
WebSocket 网络线程：独立 asyncio 事件循环 + 自动重连。
所有收到的帧通过 on_event 回调抛给调用方（回调在网路线程中执行，
GUI 侧请自行投递到主线程，例如 queue.Queue + after 轮询）。
"""
import asyncio
import json
import logging
import threading

import websockets

log = logging.getLogger("unilink.net")


class NetClient:
    def __init__(self, url: str, hello: dict, on_event):
        """
        :param url:      ws(s)://host:port/ws
        :param hello:    连接成功后立即发送的 hello 帧（每次重连都会重发）
        :param on_event: fn(dict) 收到任何服务器帧时回调
        """
        self.url = url
        self.hello = hello
        self.on_event = on_event
        self.loop = None
        self.ws = None
        self._stop = threading.Event()
        self.connected = False

    # ---------- 对 GUI 暴露的接口 ----------

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="unilink-net")
        t.start()

    def stop(self):
        self._stop.set()
        if self.loop and self.ws is not None:
            try:
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)
            except Exception:
                pass

    def send(self, obj: dict):
        """线程安全；未连接时静默丢弃"""
        if self.loop and self.ws is not None and self.connected:
            try:
                asyncio.run_coroutine_threadsafe(self._send(obj), self.loop)
            except Exception:
                pass

    # ---------- 内部实现 ----------

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:
            log.exception("net loop crashed: %r", e)

    async def _main(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                        self.url,
                        max_size=64 * 1024 * 1024,
                        ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    await ws.send(json.dumps(self.hello, ensure_ascii=False))
                    self.connected = True
                    backoff = 1.0
                    self._emit({"type": "_net", "state": "connected"})
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        self._emit(msg)
            except Exception as e:
                self._emit({"type": "_net", "state": "error",
                            "error": getattr(e, "message", None) or str(e)})
            finally:
                self.connected = False
                self.ws = None
            if self._stop.is_set():
                break
            self._emit({"type": "_net", "state": "reconnecting", "delay": int(backoff)})
            for _ in range(int(backoff * 5)):
                if self._stop.is_set():
                    return
                await asyncio.sleep(0.2)
            backoff = min(backoff * 2, 30)

    async def _send(self, obj: dict):
        try:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))
        except Exception as e:
            log.debug("send failed: %r", e)

    def _emit(self, msg: dict):
        try:
            self.on_event(msg)
        except Exception:
            log.exception("on_event callback failed")
