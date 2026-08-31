# -*- coding: utf-8 -*-
"""
端到端加密盒（与 Android 端 CryptoBox.kt 算法完全一致）

密钥派生: PBKDF2-HMAC-SHA256(口令=访问令牌, 盐="unilink|"+房间码, 迭代=120000, 32字节)
加密    : AES-256-GCM，随机 12 字节 IV，128 位 Tag
数据格式: base64( IV[12] || 密文 || Tag )
"""
import base64
import hashlib
import os

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

PBKDF2_ITER = 120_000


def derive_key(room: str, token: str) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", (token or "").encode("utf-8"),
        ("unilink|" + room).encode("utf-8"),
        PBKDF2_ITER, dklen=32)


class CryptoBox:
    def __init__(self, room: str, token: str):
        self.key = derive_key(room, token)

    def seal(self, plaintext: bytes) -> str:
        nonce = os.urandom(12)
        ct = AESGCM(self.key).encrypt(nonce, plaintext, None)
        return base64.b64encode(nonce + ct).decode("ascii")

    def open(self, blob: str) -> bytes:
        raw = base64.b64decode(blob)
        if len(raw) < 13:
            raise ValueError("密文过短")
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], None)
