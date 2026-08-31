# -*- coding: utf-8 -*-
"""
JWS 签名与 JWKS 暴露
====================
ID Token 用 RS256 签名（authentik 作为 RP 会通过 JWKS 端点验签）。
私钥首次启动时自动生成并持久化到 keys/oidc-rsa.pem，重启后复用，
否则重启会导致已签发的 ID Token 全部失效。
"""
import base64
import json
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KEY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")
KEY_PATH = os.path.join(KEY_DIR, "oidc-rsa.pem")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_json(obj) -> str:
    return b64u(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _int_to_b64u(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return b64u(n.to_bytes(length, "big"))


class Signer:
    def __init__(self, key_path: str = KEY_PATH):
        self.key_path = key_path
        self.private_key = self._load_or_create()
        self.kid = self._compute_kid()

    # ---------- 密钥 ----------

    def _load_or_create(self):
        if os.path.exists(self.key_path):
            with open(self.key_path, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None)

        os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption())
        # 先以 0600 创建文件再写入，避免出现短暂的世界可读窗口
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        print("[初始化] 已生成 OIDC 签名私钥: %s（请勿提交到版本库）" % self.key_path)
        return key

    def _compute_kid(self) -> str:
        pub = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        d = hashes.Hash(hashes.SHA256())
        d.update(pub)
        return b64u(d.finalize())[:16]

    # ---------- JWS ----------

    def sign(self, claims: dict) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        signing_input = (b64u_json(header) + "." + b64u_json(claims)).encode("ascii")
        sig = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return signing_input.decode("ascii") + "." + b64u(sig)

    def id_token(self, issuer: str, client_id: str, sub: str,
                 ttl: int, extra: dict = None, nonce: str = "") -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "sub": sub,
            "aud": client_id,
            "iat": now,
            "exp": now + ttl,
            "auth_time": now,
        }
        if nonce:
            claims["nonce"] = nonce
        if extra:
            claims.update(extra)
        return self.sign(claims)

    # ---------- JWKS ----------

    def jwks(self) -> dict:
        nums = self.private_key.public_key().public_numbers()
        return {"keys": [{
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _int_to_b64u(nums.n),
            "e": _int_to_b64u(nums.e),
        }]}
