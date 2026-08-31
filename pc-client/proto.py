# -*- coding: utf-8 -*-
"""UniLink 应用层协议封装（PC 端）"""
import json
import time
import uuid


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def envelope(from_id: str, from_name: str, platform: str,
             kind: str, payload: dict, crypto=None) -> dict:
    """构造业务消息信封；crypto 不为 None 时启用加密"""
    env = {
        "type": "msg",
        "id": new_id(),
        "ts": now_ms(),
        "from": {"id": from_id, "name": from_name, "platform": platform},
        "to": "all",
        "kind": kind,
    }
    if crypto is not None:
        env["enc"] = True
        env["payload_enc"] = crypto.seal(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    else:
        env["enc"] = False
        env["payload"] = payload
    return env


def decrypt_payload(env: dict, crypto=None):
    """返回 (payload_dict, err_str)；err 为 None 表示成功"""
    if env.get("enc"):
        if crypto is None:
            return None, "收到加密消息，但本端未启用加密"
        try:
            raw = crypto.open(env.get("payload_enc", ""))
            return json.loads(raw.decode("utf-8")), None
        except Exception as e:
            return None, "解密失败: %s" % e
    p = env.get("payload")
    return (p if isinstance(p, dict) else {}), None


KIND_TEXT = "text"
KIND_NOTIFY = "notify"
KIND_CLIPBOARD = "clipboard"
KIND_FILE_META = "file-meta"
KIND_FILE_CHUNK = "file-chunk"
KIND_FILE_END = "file-end"
KIND_NOTIFY_ACTION = "notify-action"      # 电脑 → 手机：回复某条通知
KIND_NOTIFY_ACK = "notify-action-ack"     # 手机 → 电脑：回复执行结果
KIND_CAPABILITY = "capability"            # 手机 → 电脑：能力表（适配规则+无障碍状态）
