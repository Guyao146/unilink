# -*- coding: utf-8 -*-
"""
UniLink 扫码登录服务配置
=========================
配置来源优先级：环境变量 > config.json > 内置默认值。

必填项（缺失时启动即报错，避免带着不安全默认值上线）：
  UNILINK_BASE_URL          本服务对外可访问的根地址，如 https://qr.example.com
  UNILINK_AUTHENTIK_URL     authentik 根地址，如 https://auth.example.com
  UNILINK_CLIENTS           下游客户端列表（即 authentik 的 OAuth Source）

CLIENTS 结构（config.json 中）：
  "clients": [
    {
      "client_id": "authentik",
      "client_secret": "……高强度随机串……",
      "redirect_uris": ["https://auth.example.com/source/oauth/callback/unilink-qr/"],
      "name": "authentik"
    }
  ]
"""
import json
import os
import secrets
from dataclasses import dataclass, field

CFG_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(CFG_DIR, "config.json")


class ConfigError(RuntimeError):
    pass


@dataclass
class Client:
    """下游 OIDC 客户端（正常情况下只有 authentik 一个）"""
    client_id: str
    client_secret: str
    redirect_uris: tuple
    name: str = ""

    def check_secret(self, given: str) -> bool:
        return secrets.compare_digest(self.client_secret, given or "")

    def allows(self, redirect_uri: str) -> bool:
        # 精确匹配，不做前缀/通配，避免开放重定向
        return redirect_uri in self.redirect_uris


@dataclass
class Config:
    base_url: str
    authentik_url: str
    clients: dict = field(default_factory=dict)

    host: str = "0.0.0.0"
    port: int = 8790

    # 手机 App 自身在 authentik 上注册的 public client（用于 App 端 PKCE 登录）
    app_client_id: str = "unilink-mobile"
    app_redirect_uri: str = "unilink://auth/callback"
    # offline_access 用于取得 refresh_token，让 App 能静默续期而不必反复登录
    app_scopes: str = "openid profile email offline_access"

    # 时效（秒）
    login_ttl: int = 180        # 二维码 / 登录会话有效期
    code_ttl: int = 60          # 授权码有效期
    token_ttl: int = 300        # 本服务签发的 access_token 有效期
    id_token_ttl: int = 300

    # 同时存活的扫码会话上限 —— 防止有人反复打开 /authorize 打爆内存
    max_sessions: int = 500

    # 仅允许这些 authentik 用户 sub 扫码登录；空集合表示不限制
    allowed_subs: frozenset = frozenset()
    # 仅允许这些 authentik 组扫码登录；空集合表示不限制
    allowed_groups: frozenset = frozenset()

    # ---------- 派生地址 ----------

    @property
    def issuer(self) -> str:
        return self.base_url

    @property
    def ak_userinfo(self) -> str:
        return self.authentik_url + "/application/o/userinfo/"

    @property
    def ak_token(self) -> str:
        return self.authentik_url + "/application/o/token/"

    def client(self, cid: str):
        return self.clients.get(cid)


def _env(key: str, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def _norm(url: str) -> str:
    return (url or "").rstrip("/")


def load() -> Config:
    raw = {}
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)

    base_url = _norm(_env("UNILINK_BASE_URL", raw.get("base_url", "")))
    authentik_url = _norm(_env("UNILINK_AUTHENTIK_URL", raw.get("authentik_url", "")))

    if not base_url:
        raise ConfigError("缺少 base_url（或环境变量 UNILINK_BASE_URL）"
                          "：本服务对外可访问的根地址，例如 https://qr.example.com")
    if not authentik_url:
        raise ConfigError("缺少 authentik_url（或环境变量 UNILINK_AUTHENTIK_URL）"
                          "：authentik 根地址，例如 https://auth.example.com")

    clients_raw = raw.get("clients") or []
    env_clients = _env("UNILINK_CLIENTS")
    if env_clients:
        clients_raw = json.loads(env_clients)

    clients = {}
    for c in clients_raw:
        cid = str(c.get("client_id") or "").strip()
        sec = str(c.get("client_secret") or "")
        uris = tuple(c.get("redirect_uris") or ())
        if not cid or not sec or not uris:
            raise ConfigError("clients 中每一项都必须含 client_id / client_secret / redirect_uris")
        if len(sec) < 24:
            raise ConfigError("client_secret 长度不足 24 位，请使用高强度随机串"
                              "（可用 python -c \"import secrets;print(secrets.token_urlsafe(48))\" 生成）")
        clients[cid] = Client(cid, sec, uris, str(c.get("name") or cid))

    if not clients:
        raise ConfigError("clients 为空：至少需要注册 authentik 作为下游客户端，"
                          "参见 auth-server/config.example.json")

    cfg = Config(base_url=base_url, authentik_url=authentik_url, clients=clients)

    cfg.host = _env("UNILINK_HOST", raw.get("host", cfg.host))
    cfg.port = int(_env("UNILINK_PORT", raw.get("port", cfg.port)))
    cfg.app_client_id = _env("UNILINK_APP_CLIENT_ID",
                             raw.get("app_client_id", cfg.app_client_id))
    cfg.app_redirect_uri = _env("UNILINK_APP_REDIRECT_URI",
                                raw.get("app_redirect_uri", cfg.app_redirect_uri))
    cfg.login_ttl = int(raw.get("login_ttl", cfg.login_ttl))
    cfg.code_ttl = int(raw.get("code_ttl", cfg.code_ttl))
    cfg.max_sessions = int(raw.get("max_sessions", cfg.max_sessions))
    cfg.app_scopes = raw.get("app_scopes", cfg.app_scopes)
    cfg.allowed_subs = frozenset(raw.get("allowed_subs") or ())
    cfg.allowed_groups = frozenset(raw.get("allowed_groups") or ())

    if base_url.startswith("http://") and not base_url.startswith("http://127.0.0.1"):
        print("[警告] base_url 不是 https —— 授权码与令牌将以明文传输，"
              "生产环境请务必用 Nginx/Caddy 反代为 https")

    return cfg
