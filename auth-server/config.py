# -*- coding: utf-8 -*-
"""
UniLink 扫码登录服务配置
=========================
配置来源优先级：环境变量 > config.json > 内置默认值。
Docker 部署只需维护 compose 同目录的一份 .env，所有项都能在其中修改；
config.json 仅在不使用 Docker 时才需要。

必填项（缺失时启动即报错，避免带着不安全默认值上线）：
  UNILINK_BASE_URL          本服务对外可访问的根地址，如 https://qr.example.com
  UNILINK_AUTHENTIK_URL     authentik 根地址，如 https://auth.example.com
  UNILINK_CLIENT_SECRET     下游客户端密钥（与 authentik OAuth Source 的 Consumer secret 一致）
  UNILINK_CLIENTS           下游客户端列表（即 authentik 的 OAuth Source）

CLIENTS 结构（config.json 中，或 compose 拼装的 UNILINK_CLIENTS 环境变量）：
  "clients": [
    {
      "client_id": "authentik",
      "client_secret": "……高强度随机串……",
      "redirect_uris": ["https://auth.example.com/source/oauth/callback/unilink-qr/"],
      "name": "authentik"
    }
  ]

所有可选项同样可用环境变量覆盖，例如：
  UNILINK_LOGIN_TTL / UNILINK_CODE_TTL / UNILINK_TOKEN_TTL
  UNILINK_MAX_SESSIONS / UNILINK_APP_SCOPES
  UNILINK_ALLOWED_SUBS / UNILINK_ALLOWED_GROUPS（逗号或空白分隔多个值）
"""
import json
import os
import secrets
from dataclasses import dataclass, field

CFG_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(CFG_DIR, "config.json")

# 网页后台（首次配置向导 / 管理面板）写入的持久化配置。
# 容器里挂成卷，重建不丢；优先级低于环境变量与本地 config.json。
STATE_DIR = (os.environ.get("UNILINK_STATE_DIR")
             or os.path.join(CFG_DIR, "data"))
STATE_PATH = os.path.join(STATE_DIR, "state.json")


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


def _int(key: str, default):
    """整数型配置项。格式不对时报清楚的错，而不是抛 ValueError 让人摸不着头脑"""
    v = _env(key)
    if v is None:
        return default
    try:
        return int(str(v).strip())
    except ValueError:
        raise ConfigError("%s 必须是整数（当前值 %r）" % (key, v))


def _frozenset(val):
    """白名单既可以是 .env 里的逗号/空白分隔字符串，也可以是 config.json 里的列表"""
    if not val:
        return frozenset()
    items = val.replace(",", " ").split() if isinstance(val, str) else val
    return frozenset(str(t).strip() for t in items if str(t).strip())


def _norm(url: str) -> str:
    return (url or "").rstrip("/")


def _read_json(path: str) -> dict:
    """读配置文件；不存在返回空 dict，损坏时报清楚的错"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        raise ConfigError("配置文件 %s 无法解析：%s" % (path, e))


def save_state(data: dict) -> None:
    """网页后台保存配置。先写临时文件再原子替换，避免并发读到半截 JSON。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


def load() -> Config:
    # 优先级：环境变量 > 本地 config.json（开发者/部署者手填）> 网页后台 state
    raw = dict(_read_json(STATE_PATH))
    raw.update(_read_json(CFG_PATH))

    base_url = _norm(_env("UNILINK_BASE_URL", raw.get("base_url", "")))
    authentik_url = _norm(_env("UNILINK_AUTHENTIK_URL", raw.get("authentik_url", "")))

    if not base_url:
        raise ConfigError("缺少 base_url（或环境变量 UNILINK_BASE_URL）"
                          "：本服务对外可访问的根地址，例如 https://qr.example.com")
    if not authentik_url:
        raise ConfigError("缺少 authentik_url（或环境变量 UNILINK_AUTHENTIK_URL）"
                          "：authentik 根地址，例如 https://auth.example.com")

    clients_raw = []
    env_clients = _env("UNILINK_CLIENTS")
    if env_clients:
        clients_raw = json.loads(env_clients)
    elif raw.get("clients"):
        clients_raw = raw["clients"]
    else:
        # 既没有 UNILINK_CLIENTS 也没有 clients 数组时，用零散字段拼装一个 ——
        # 覆盖「.env 只填单值」和「网页后台填 client_id + secret」两种情形
        cid = str(_env("UNILINK_CLIENT_ID",
                       raw.get("client_id", "unilink-qr")) or "unilink-qr").strip()
        sec = str(_env("UNILINK_CLIENT_SECRET",
                       raw.get("client_secret", "")) or "")
        if cid and sec:
            clients_raw = [{
                "name": "authentik",
                "client_id": cid,
                "client_secret": sec,
                "redirect_uris": [authentik_url + "/source/oauth/callback/" + cid + "/"],
            }]

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

    # 每一项都允许「环境变量 > config.json > 代码内默认值」。
    # Docker 部署时只需维护一份 .env，不必再往镜像里塞 config.json。
    cfg.host = _env("UNILINK_HOST", raw.get("host", cfg.host))
    cfg.port = _int("UNILINK_PORT", raw.get("port", cfg.port))
    cfg.app_client_id = _env("UNILINK_APP_CLIENT_ID",
                             raw.get("app_client_id", cfg.app_client_id))
    cfg.app_redirect_uri = _env("UNILINK_APP_REDIRECT_URI",
                                raw.get("app_redirect_uri", cfg.app_redirect_uri))
    cfg.app_scopes = _env("UNILINK_APP_SCOPES",
                          raw.get("app_scopes", cfg.app_scopes))
    cfg.login_ttl = _int("UNILINK_LOGIN_TTL", raw.get("login_ttl", cfg.login_ttl))
    cfg.code_ttl = _int("UNILINK_CODE_TTL", raw.get("code_ttl", cfg.code_ttl))
    cfg.token_ttl = _int("UNILINK_TOKEN_TTL", raw.get("token_ttl", cfg.token_ttl))
    cfg.id_token_ttl = _int("UNILINK_ID_TOKEN_TTL",
                            raw.get("id_token_ttl", cfg.id_token_ttl))
    cfg.max_sessions = _int("UNILINK_MAX_SESSIONS",
                            raw.get("max_sessions", cfg.max_sessions))
    cfg.allowed_subs = _frozenset(_env("UNILINK_ALLOWED_SUBS",
                                       raw.get("allowed_subs")))
    cfg.allowed_groups = _frozenset(_env("UNILINK_ALLOWED_GROUPS",
                                         raw.get("allowed_groups")))

    if base_url.startswith("http://") and not base_url.startswith("http://127.0.0.1"):
        print("[警告] base_url 不是 https —— 授权码与令牌将以明文传输，"
              "生产环境请务必用 Nginx/Caddy 反代为 https")

    return cfg


def load_or_none():
    """已配置返回 Config；缺必填项返回 None（调用方据此进入首次配置模式）"""
    try:
        return load()
    except ConfigError:
        return None
