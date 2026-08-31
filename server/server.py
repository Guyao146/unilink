#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UniLink 中继服务器
------------------
只负责在同一个“房间”内的设备之间转发消息，不解析业务内容。
配合端到端加密时，服务器完全看不到消息明文。

用法:
    pip install -r requirements.txt
    python server.py --port 8765 [--token 你的访问口令]

客户端连接地址: ws://<本机IP>:8765/ws
"""
import argparse
import asyncio
import hmac
import json
import logging
import re
import sys
import uuid

try:
    import websockets
except ImportError:
    print("请先安装依赖: pip install websockets")
    sys.exit(1)

log = logging.getLogger("unilink")

ROOM_RE = re.compile(r"^[\w\-]{4,32}$")
TOKEN = ""


class Peer:
    __slots__ = ("ws", "id", "name", "platform", "crypto_cap")

    def __init__(self, ws, name, platform, crypto_cap):
        self.ws = ws
        self.id = uuid.uuid4().hex[:8]
        self.name = (name or self.id)[:32]
        self.platform = (platform or "?")[:16]
        self.crypto_cap = bool(crypto_cap)

    def public(self):
        return {"id": self.id, "name": self.name, "platform": self.platform}


class Room:
    def __init__(self, code):
        self.code = code
        self.peers = {}      # id -> Peer
        self.crypto = False  # 房间是否启用端到端加密（所有成员都具备能力才开启）

    def recompute_crypto(self):
        """返回值表示加密状态是否发生变化"""
        val = all(p.crypto_cap for p in self.peers.values()) if self.peers else False
        changed = val != self.crypto
        self.crypto = val
        return changed


rooms = {}  # code -> Room


def room_of(code: str) -> Room:
    r = rooms.get(code)
    if r is None:
        r = Room(code)
        rooms[code] = r
    return r


async def _send(peer: Peer, obj: dict):
    try:
        await peer.ws.send(json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


async def _bcast(room: Room, obj: dict, exclude: str = None):
    txt = json.dumps(obj, ensure_ascii=False)
    dead = []
    for p in list(room.peers.values()):
        if exclude and p.id == exclude:
            continue
        try:
            await p.ws.send(txt)
        except Exception:
            dead.append(p.id)
    for pid in dead:
        room.peers.pop(pid, None)


async def handler(ws, path=None):
    """兼容 websockets 旧版 (ws, path) 与新版 (ws) 两种回调签名"""
    peer = None
    room = None
    try:
        # ---- 第一帧必须是 hello ----
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        hello = json.loads(raw)
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.send(json.dumps({"type": "error", "message": "第一帧必须是 hello"}))
            return

        code = str(hello.get("room") or "")
        if not ROOM_RE.match(code):
            await ws.send(json.dumps({"type": "error",
                                      "message": "房间码需为 4-32 位字母/数字/-/_"}))
            return
        if TOKEN and not hmac.compare_digest(str(hello.get("token") or ""), TOKEN):
            await ws.send(json.dumps({"type": "error", "message": "访问令牌错误"}))
            return

        peer = Peer(ws, str(hello.get("name") or ""),
                    str(hello.get("platform") or ""),
                    bool(hello.get("crypto_cap")))
        room = room_of(code)
        room.peers[peer.id] = peer

        changed = room.recompute_crypto()
        await _send(peer, {
            "type": "welcome",
            "you": peer.id,
            "peers": [p.public() for p in room.peers.values() if p.id != peer.id],
            "crypto": room.crypto,
        })
        await _bcast(room, {"type": "peer_joined",
                            "peer": peer.public(), "crypto": room.crypto},
                     exclude=peer.id)
        if changed and len(room.peers) > 1:
            await _bcast(room, {"type": "crypto_changed", "crypto": room.crypto})

        log.info("[%s] %s(%s) 加入  成员=%d  加密=%s",
                 code, peer.name, peer.id, len(room.peers), room.crypto)

        # ---- 主循环：仅转发 type=msg 的业务帧 ----
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") != "msg":
                continue  # 其它控制帧一律丢弃，防止伪造
            msg["from"] = peer.public()
            msg.pop("token", None)
            to = msg.get("to") or "all"
            if to == "all":
                await _bcast(room, msg)
            else:
                dst = room.peers.get(to)
                if dst:
                    await _send(dst, msg)
    except asyncio.TimeoutError:
        pass
    except Exception as e:  # noqa
        log.debug("handler error: %r", e)
    finally:
        if room and peer:
            room.peers.pop(peer.id, None)
            changed = room.recompute_crypto()
            await _bcast(room, {"type": "peer_left",
                                "peer_id": peer.id, "crypto": room.crypto})
            if changed and room.peers:
                await _bcast(room, {"type": "crypto_changed", "crypto": room.crypto})
            log.info("[%s] %s 离开  剩余=%d", room.code, peer.id, len(room.peers))
            if not room.peers:
                rooms.pop(room.code, None)


async def main():
    global TOKEN
    ap = argparse.ArgumentParser(description="UniLink 中继服务器")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    ap.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    ap.add_argument("--token", default="", help="访问令牌；留空则不校验")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    TOKEN = a.token

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    async with websockets.serve(handler, a.host, a.port,
                                max_size=64 * 1024 * 1024,
                                ping_interval=20, ping_timeout=20):
        log.info("UniLink 服务器已启动: ws://<局域网IP>:%d/ws%s",
                 a.port, "  （已启用令牌校验）" if TOKEN else "")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
