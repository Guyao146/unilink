# 扫码登录部署指南

让所有已接入 authentik 的项目获得「手机扫码登录」，无需逐个改造。

## 原理

`auth-server` 是一个**上游 OIDC Provider**，注册进 authentik 后成为一个登录源
（Source）。authentik 会自动把它作为按钮显示在登录页上，而下游项目仍然只跟
authentik 打交道 —— 它们完全不知道扫码这件事存在。

```
浏览器            auth-server              authentik          手机 App
  │ 点“扫码登录”      │                       │                  │
  │ ─────────────────────────────────────────►│                  │
  │                  │◄── 重定向 /authorize ──┤                  │
  │◄── 二维码页面 ───┤                       │                  │
  │                  │◄──── 扫码 + 确认（附 authentik 令牌）─────┤
  │                  │── 用令牌查 userinfo ──►│                  │
  │◄── 轮询到 code ──┤                       │                  │
  │ ── code 回调 ────────────────────────────►│                  │
  │                  │◄── /token 换 id_token ─┤                  │
  │◄──────────── 登录完成，回到原项目 ─────────┤                  │
```

信任链的关键：**auth-server 从不自行判断用户身份**。手机递上 authentik 的
access_token，auth-server 拿它去问 authentik「这是谁」。伪造令牌换不出
userinfo，令牌被吊销立刻失效。签发的 `sub` 直接沿用 authentik 的 `sub`，
因此回连时能匹配到同一个用户，不会重复建号。

## 一、部署 auth-server

以下三种方式任选其一。**推荐 Docker**（与 authentik 部署方式一致，升级回滚都简单）。

无论哪种方式，都要先准备一个客户端密钥：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```

记下这串，第二步在 authentik 里要填同一个值。

---

### 方式 A：Docker Compose（推荐）

```bash
# 1. 拉代码
cd /opt
git clone https://github.com/Guyao146/unilink.git
cd unilink/auth-server

# 2. 配置
cp .env.example .env
vi .env          # 填 UNILINK_CLIENT_SECRET，核对两个 URL

# 3. 启动
docker compose up -d --build
docker compose logs -f
```

看到这几行就说明起来了：

```
UniLink 扫码登录服务已启动
  issuer            : https://gateway.mcylyr.cn
  authentik         : https://login.mcylyr.cn
  已注册客户端      : unilink-qr
  签名 kid          : xxxxxxxx
```

本机自检：

```bash
curl -i http://127.0.0.1:8790/healthz
# 预期 200 {"ok": true, "sessions": 0, "codes": 0, "tokens": 0}
```

> 容器只绑 `127.0.0.1:8790`，不会直接暴露到公网 —— 必须经第 1.5 步的反代出去。
> `keys/` 挂成了宿主目录，RSA 签名私钥不会随容器重建而丢失。

**authentik 也在 Docker 里？** 把 compose 文件末尾的 `networks` 段取消注释，
改成 authentik 的网络名（`docker network ls` 查），然后 `.env` 里的
`UNILINK_AUTHENTIK_URL` 可以改用容器名如 `http://authentik-server:9000`，
省掉出公网绕一圈。注意此时 `UNILINK_BASE_URL` 仍必须是对外的 https 地址。

---

### 方式 B：systemd（不想用 Docker）

```bash
# 1. 代码与虚拟环境
cd /opt
git clone https://github.com/Guyao146/unilink.git
cd unilink/auth-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 专用系统用户（不给登录 shell）
useradd --system --no-create-home --shell /usr/sbin/nologin unilink

# 3. 配置文件，权限收紧到 0600（里面有客户端密钥）
cp .env.example .env
vi .env
chmod 600 .env
mkdir -p keys
chown -R unilink:unilink /opt/unilink/auth-server

# 4. 注册服务
cp unilink-auth.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now unilink-auth
systemctl status unilink-auth
journalctl -u unilink-auth -f
```

自检同上：`curl -i http://127.0.0.1:8790/healthz`

---

### 方式 C：先手动跑一遍（只为验证配置）

```bash
cd /opt/unilink/auth-server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.json config.json
vi config.json                    # 填 4 个值，见下表
.venv/bin/python app.py
```

`config.json` 必填项：

| 字段 | 值 |
|------|-----|
| `base_url` | `https://gateway.mcylyr.cn` — 本服务对外地址，**必须 https** |
| `authentik_url` | `https://login.mcylyr.cn` — authentik 根地址 |
| `clients[0].client_secret` | 上面生成的随机串 |
| `clients[0].redirect_uris` | `https://login.mcylyr.cn/source/oauth/callback/unilink-qr/` |

配置有误时它会打印 `[配置错误] ...` 并退出，照提示改即可。
这种方式关掉终端服务就停了，验证完请转 A 或 B。

## 一点五、反向代理

auth-server 只听 `127.0.0.1:8790`，需要 nginx 把子域转进来。
完整可用的配置见 **`auth-server/nginx.conf.example`**，核心是：

```nginx
server {
    listen 443 ssl;              # ← ssl 关键字漏了就会 TLS 握手失败
    server_name gateway.mcylyr.cn;

    ssl_certificate     /etc/letsencrypt/live/mcylyr.cn/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcylyr.cn/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8790;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`nginx -t && nginx -s reload` 后从**外部**验证：

```bash
curl -i https://gateway.mcylyr.cn/api/app/config
```

预期 200 + 含 `authorize_url`、`client_id` 的 JSON。对照排查：

| 现象 | 原因 |
|------|------|
| TLS 握手失败 / `SEC_E_INVALID_TOKEN` | `listen 443` 漏了 `ssl`，或该 vhost 没配证书 |
| 502 | auth-server 没起来（回去看 `docker compose logs` / `journalctl`） |
| 404 且无 `X-Powered-By` | 落到了 nginx 默认 server，`server_name` 没匹配上 |
| 响应头有 `X-Powered-By: authentik` | 请求打到了 authentik —— 域名或 proxy_pass 配错了 |

> Caddy 用户更省事，一行足够：
> `gateway.mcylyr.cn { reverse_proxy 127.0.0.1:8790 }`

## 二、在 authentik 里加为登录源

**Directory → Federation & Social login → Create → OpenID Connect OAuth Source**

| 字段 | 值 |
|------|-----|
| Name | `手机扫码登录`（会显示在登录页按钮上） |
| Slug | `unilink-qr` ← 必须与 `redirect_uris` 里的一致 |
| Consumer key | `unilink-qr`（config.json 的 `client_id`） |
| Consumer secret | config.json 的 `client_secret` |
| OIDC Well-known URL | `https://qr.example.com/.well-known/openid-configuration` |
| Scopes | `openid profile email` |

保存后 authentik 会自动抓取发现文档填好各端点。若报错，先确认这个 URL
在 authentik 容器内部可访问（Docker 部署时 `qr.example.com` 需能解析）。

**把源挂到登录流程上**：Flows → `default-authentication-flow` → Stage Bindings
→ 找到 `default-authentication-identification` → Edit → 在 **Sources** 里勾选刚建的源。

现在 authentik 登录页上就会出现「手机扫码登录」按钮了，所有下游项目通用。

## 三、为手机 App 建一个 OAuth2 Provider

App 自身也要登录 authentik（拿到那张"身份凭证"），走标准的
Authorization Code + PKCE。

**Applications → Providers → Create → OAuth2/OpenID Provider**

| 字段 | 值 |
|------|-----|
| Name | `UniLink 手机端` |
| Client type | **Public** ← 手机 App 无法保管密钥，必须是 public + PKCE |
| Client ID | `unilink-mobile`（与 config.json 的 `app_client_id` 一致） |
| Redirect URIs | `unilink://auth/callback` |
| Scopes | `openid`、`profile`、`email`、`offline_access` |

`offline_access` 用于签发 refresh_token，让 App 能静默续期而不必反复登录。

再建一个 Application 关联这个 Provider（slug 随意，如 `unilink`），
并按需绑定策略限制谁能用。

> 注意：Redirect URI 用的是自定义 scheme。Android 上自定义 scheme 可被
> 恶意应用抢注，因此 PKCE 是**必需**而非可选 —— 即使授权码被截获，
> 没有 code_verifier 也换不出令牌。App 端已强制 S256。

## 四、手机端使用

1. 在 UniLink 主界面「authentik 账号」区域填入 `https://qr.example.com`
2. 点 **登录 authentik** → 系统浏览器打开授权页 → 完成登录后自动回到 App
3. 之后任意时刻点 **扫码登录**，对准电脑上的二维码
4. 手机弹出确认框，显示"将以【谁】的身份登录【哪个应用】"
5. 点确认 → 电脑上自动完成登录

登录状态加密保存在本地（Android Keystore 硬件密钥 + AES-GCM），
重启 App 仍然有效。点「退出」清除本地令牌。

## 排障

| 现象 | 处理 |
|------|------|
| authentik 保存 Source 时报无法获取 well-known | authentik 容器内无法解析/访问 `base_url`。Docker 部署时检查 DNS 或用内网地址 |
| 登录页没有扫码按钮 | Source 没绑定到 identification stage（见第二步最后一段） |
| 扫码后 App 提示"不是 UniLink 登录码" | 二维码里的 `srv` 不是 https。生产环境请配好 TLS |
| App 提示"服务器不一致" | 二维码指向的服务与 App 登录时用的不是同一个 —— 正常情况下这是钓鱼拦截 |
| 确认时提示"令牌已失效" | authentik 侧 refresh_token 过期或被吊销，重新登录即可。若频繁出现，检查 Provider 是否给了 `offline_access` |
| `invalid_client` | config.json 的 `client_secret` 与 authentik Source 里的 Consumer secret 不一致 |
| 二维码总是过期 | 默认 180 秒。若网络慢可调大 `config.json` 的 `login_ttl` |
| 换机后无法登录 | Keystore 密钥不随备份迁移，属预期行为，重新登录即可 |

## 安全边界

* auth-server 只是**中转身份**，不存密码、不存长期令牌（内存会话 3 分钟即过期）。
* 二维码 ticket 是 32 字节随机串，授权码一次性且 60 秒过期。
* **授权码只交给发起登录的那个浏览器**：页面里另有一个不进二维码的
  `poll_secret`，轮询取码必须出示它。因此别人拍下或截屏二维码也拿不到 code ——
  二维码本身只够用来"确认"，不足以"取码"。
* 扫码必须经手机上的人工确认 —— 这是防"把二维码摆到别人面前"的唯一有效手段。
* 手机端拒绝向公网 http 地址发送令牌，也拒绝指向非登录服务器的二维码。
* 手机端令牌用 Android Keystore 硬件密钥加密后落盘，并已从云备份中排除
  （密钥不参与备份，备份出去也解不开）。
* 同时存活的扫码会话有上限（默认 500，见 `max_sessions`），防止刷 `/authorize` 打爆内存。
* 单实例内存存储：重启会让正在扫码的用户重试一次。要多实例横向扩展需把
  `store.py` 换成 Redis 实现。
* `keys/` 与 `config.json` 已在 `.gitignore` 中，切勿提交。

## 附录：二维码与接口约定

二维码内容是一段紧凑 JSON（**不含**浏览器轮询密钥）：

```json
{"v":1,"typ":"unilink-login","srv":"https://qr.example.com","tk":"<32字节随机串>"}
```

手机 App 侧接口（都在 `auth-server` 上）：

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/app/config` | 取 authentik 端点与 App 的 client_id |
| POST | `/api/scan/preview` | `{ticket, device}` → 返回将登录的应用名，会话转为 scanned |
| POST | `/api/scan/approve` | `{ticket, access_token}` → 校验身份并生成授权码 |
| POST | `/api/scan/deny` | `{ticket}` → 拒绝本次登录 |

浏览器侧：

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/authorize` | authentik 重定向入口，返回二维码页面 |
| GET | `/api/session/{ticket}?k=<轮询密钥>` | 轮询状态，approved 时返回 code |

标准 OIDC 端点：`/.well-known/openid-configuration`、`/token`、`/userinfo`、`/jwks`。
`/healthz` 返回当前会话数，可用于监控。

## 测试

```bash
cd unilink/auth-server
python -m unittest test_flow -v     # 31 项，含完整流程与各类攻击场景
```

手机端的二维码解析与授权 URL 构造也有 JVM 单元测试（无需设备）：

```bash
cd unilink/android
gradle testDebugUnitTest
```

两者都已接入 GitHub Actions，见 `.github/workflows/`。
