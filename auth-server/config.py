# -*- coding: utf-8 -*-
"""
UniLink 扫码登录服务配置
=========================
配置来源优先级：网页后台 state > 环境变量 > 本地 config.json > 内置默认值。
即：在 /setup 或 /admin 面板里改过的值一定生效；面板没配过的项才由 .env / 环境变量
提供引导默认值。Docker 部署时维护 compose 同目录的一份 .env 即可，所有项都能在其中
修改；面板里的修改会覆盖 .env 中同名的项。

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


def _int_val(key: str, val, default):
    """整数型配置项。格式不对时报清楚的错，而不是抛 ValueError 让人摸不着头脑。
    val 可能是字符串（.env / 环境变量）也可能是 int（config.json / 后台写入）"""
    if val is None or (isinstance(val, str) and not val.strip()):
        return default
    try:
        return int(str(val).strip())
    except ValueError:
        raise ConfigError("%s 必须是整数（当前值 %r）" % (key, val))


# 环境变量名 -> 配置字段名。网页后台改的值优先于这些环境变量（见 _apply_state）
_ENV_FIELDS = {
    "base_url": "UNILINK_BASE_URL",
    "authentik_url": "UNILINK_AUTHENTIK_URL",
    "client_id": "UNILINK_CLIENT_ID",
    "client_secret": "UNILINK_CLIENT_SECRET",
    "host": "UNILINK_HOST",
    "port": "UNILINK_PORT",
    "app_client_id": "UNILINK_APP_CLIENT_ID",
    "app_redirect_uri": "UNILINK_APP_REDIRECT_URI",
    "app_scopes": "UNILINK_APP_SCOPES",
    "login_ttl": "UNILINK_LOGIN_TTL",
    "code_ttl": "UNILINK_CODE_TTL",
    "token_ttl": "UNILINK_TOKEN_TTL",
    "id_token_ttl": "UNILINK_ID_TOKEN_TTL",
    "max_sessions": "UNILINK_MAX_SESSIONS",
    "allowed_subs": "UNILINK_ALLOWED_SUBS",
    "allowed_groups": "UNILINK_ALLOWED_GROUPS",
}


def _apply_env(raw: dict) -> None:
    """把环境变量叠到 raw 上（空值不覆盖，允许显式留空走下一层）"""
    for name, env in _ENV_FIELDS.items():
        v = _env(env)
        if v is not None:
            raw[name] = v


def _apply_state(raw: dict) -> None:
    """把网页后台的 state.json 叠到 raw 上，优先级最高 —— 面板里改的值必须生效。
    空串视为「未填写」，不覆盖下层（面板里留空代表沿用现有值）"""
    for k, v in _read_json(STATE_PATH).items():
        if isinstance(v, str):
            if v.strip():
                raw[k] = v
        elif v is not None:
            raw[k] = v


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
    # 优先级：网页后台 state > 环境变量（.env / compose）> 本地 config.json > 默认值。
    # 后台面板是主配置入口，面板里改过的值必须生效；环境变量只在面板尚未配置过
    # 该项时提供引导默认值（Docker 开箱即用），二者不冲突。
    raw = _read_json(CFG_PATH)
    _apply_env(raw)
    _apply_state(raw)

    base_url = _norm(raw.get("base_url", ""))
    authentik_url = _norm(raw.get("authentik_url", ""))

    if not base_url:
        raise ConfigError("\u7f3a\u5c11 base_url\uff08\u6216\u73af\u5883\u53d8\u91cf UNILINK_BASE_URL\uff09"
                          "\uff1a\u672c\u670d\u52a1\u5bf9\u5916\u53ef\u8bbf\u95ee\u7684\u6839\u5730\u5740\uff0c\u4f8b\u5982 https://qr.example.com"
                          "\n\u8bf7\u590d\u5236 config.example.json \u4e3a config.json \u5e76\u586b\u5199\uff0c"
                          "\u6216\u8bbe\u7f6e\u5bf9\u5e94\u73af\u5883\u53d8\u91cf\u3002")
    if not authentik_url:
        raise ConfigError("\u7f3a\u5c11 authentik_url\uff08\u6216\u73af\u5883\u53d8\u91cf UNILINK_AUTHENTIK_URL\uff09"
                          "\uff1aauthentik \u6839\u5730\u5740\uff0c\u4f8b\u5982 https://auth.example.com")

    clients_raw = []
    if raw.get("clients"):
        # \u663e\u5f0f\u7684 clients \u6570\u7ec4\uff08config.json\uff0c\u6216\u540e\u53f0\u5199\u5165\u7684\u5b8c\u6574\u6570\u7ec4\uff09
        clients_raw = raw["clients"]
    elif _env("UNILINK_CLIENTS"):
        # \u9003\u751f\u53e3\uff1a\u73af\u5883\u53d8\u91cf\u76f4\u63a5\u7ed9\u5b8c\u6574 JSON \u6570\u7ec4\uff08\u591a\u5ba2\u6237\u7aef\u7b49\u9ad8\u7ea7\u573a\u666f\uff09
        clients_raw = json.loads(_env("UNILINK_CLIENTS"))
    else:
        # \u5e38\u89c4\u60c5\u5f62\uff1a\u7531 client_id + client_secret + authentik_url \u62fc\u88c5\u4e00\u4e2a\u5ba2\u6237\u7aef\uff0c
        # \u8986\u76d6\u300c.env \u53ea\u586b\u5355\u503c\u300d\u548c\u300c\u7f51\u9875\u540e\u53f0\u586b client_id + secret\u300d\u4e24\u79cd\u65b9\u5f0f\u3002
        # client_id / secret \u5df2\u6309\u4e0a\u9762\u7684\u4f18\u5148\u7ea7\u5408\u5e76\u597d\uff0c\u540e\u53f0\u6539\u7684\u503c\u5728\u8fd9\u91cc\u751f\u6548\u3002
        cid = str(raw.get("client_id", "unilink-qr") or "unilink-qr").strip()
        sec = str(raw.get("client_secret", "") or "")
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
            raise ConfigError("clients \u4e2d\u6bcf\u4e00\u9879\u90fd\u5fc5\u987b\u542b client_id / client_secret / redirect_uris")
        if len(sec) < 24:
            raise ConfigError("client_secret \u957f\u5ea6\u4e0d\u8db3 24 \u4f4d\uff0c\u8bf7\u4f7f\u7528\u9ad8\u5f3a\u5ea6\u968f\u673a\u4e32"
                              "\uff08\u53ef\u7528 python -c \"import secrets;print(secrets.token_urlsafe(48))\" \u751f\u6210\uff09")
        clients[cid] = Client(cid, sec, uris, str(c.get("name") or cid))

    if not clients:
        raise ConfigError("clients \u4e3a\u7a7a\uff1a\u81f3\u5c11\u9700\u8981\u6ce8\u518c authentik \u4f5c\u4e3a\u4e0b\u6e38\u5ba2\u6237\u7aef\uff0c"
                          "\u53c2\u89c1 auth-server/config.example.json")

    cfg = Config(base_url=base_url, authentik_url=authentik_url, clients=clients)

    # \u5404\u9879\u5df2\u6309\u300c\u7f51\u9875\u540e\u53f0 > \u73af\u5883\u53d8\u91cf > config.json\u300d\u5408\u5e76\u8fdb raw\uff0c\u76f4\u63a5\u53d6\u503c\u5373\u53ef\uff1b
    # raw \u91cc\u6ca1\u6709\u7684\u9879\u56de\u843d\u5230 dataclass \u9ed8\u8ba4\u503c\u3002
    cfg.host = str(raw.get("host") or cfg.host)
    cfg.port = _int_val("UNILINK_PORT", raw.get("port"), cfg.port)

    def pick(key, default):
        v = str(raw.get(key) or "").strip()
        return v or default

    cfg.app_client_id = pick("app_client_id", cfg.app_client_id)
    cfg.app_redirect_uri = pick("app_redirect_uri", cfg.app_redirect_uri)
    cfg.app_scopes = pick("app_scopes", cfg.app_scopes)
    cfg.login_ttl = _int_val("UNILINK_LOGIN_TTL", raw.get("login_ttl"), cfg.login_ttl)
    cfg.code_ttl = _int_val("UNILINK_CODE_TTL", raw.get("code_ttl"), cfg.code_ttl)
    cfg.token_ttl = _int_val("UNILINK_TOKEN_TTL", raw.get("token_ttl"), cfg.token_ttl)
    cfg.id_token_ttl = _int_val("UNILINK_ID_TOKEN_TTL",
                                raw.get("id_token_ttl"), cfg.id_token_ttl)
    cfg.max_sessions = _int_val("UNILINK_MAX_SESSIONS",
                                raw.get("max_sessions"), cfg.max_sessions)
    cfg.allowed_subs = _frozenset(raw.get("allowed_subs"))
    cfg.allowed_groups = _frozenset(raw.get("allowed_groups"))

    if base_url.startswith("http://") and not base_url.startswith("http://127.0.0.1"):
        print("[\u8b66\u544a] base_url \u4e0d\u662f https \u2014\u2014 \u6388\u6743\u7801\u4e0e\u4ee4\u724c\u5c06\u4ee5\u660e\u6587\u4f20\u8f93\uff0c"
              "\u751f\u4ea7\u73af\u5883\u8bf7\u52a1\u5fc5\u7528 Nginx/Caddy \u53cd\u4ee3\u4e3a https")

    return cfg


def load_or_none():
    """已配置返回 Config；缺必填项返回 None（调用方据此进入首次配置模式）"""
    try:
        return load()
    except ConfigError:
        return None
