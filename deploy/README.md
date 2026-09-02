# 一键部署（预构建镜像）

不需要 clone 源码，两个文件就能跑起来。

```bash
mkdir -p /opt/unilink-auth && cd /opt/unilink-auth

curl -O  https://raw.githubusercontent.com/Guyao146/unilink/main/deploy/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/Guyao146/unilink/main/deploy/.env.example

# 生成客户端密钥，填进 .env 的 UNILINK_CLIENT_SECRET
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
vi .env

docker compose up -d
docker compose logs -f
```

启动成功会打印：

```
UniLink 扫码登录服务已启动
  issuer            : https://gateway.example.com
  authentik         : https://login.example.com
  已注册客户端      : unilink-qr
  签名 kid          : xxxxxxxx
```

自检：

```bash
curl -i http://127.0.0.1:8790/healthz
# 200 {"ok": true, "sessions": 0, "codes": 0, "tokens": 0}
```

镜像同时提供 `linux/amd64` 与 `linux/arm64`，Docker 会自动选对应架构。

## 反向代理

服务只监听 `127.0.0.1:8790`，需要反代把子域转进来。

**Caddy**（一行）：

```
gateway.example.com {
    reverse_proxy 127.0.0.1:8790
}
```

**nginx**：完整配置见仓库 [`auth-server/nginx.conf.example`](../auth-server/nginx.conf.example)。
最常踩的坑是 `listen 443` 后面漏写 `ssl` —— 那样 nginx 会在 443 上讲明文 HTTP，
客户端 TLS 握手直接失败（`SEC_E_INVALID_TOKEN` / `wrong version number`）。

改完从**外部**验证：

```bash
curl -i https://gateway.example.com/api/app/config
```

预期 200 + 含 `authorize_url`、`client_id` 的 JSON。

| 现象 | 原因 |
|------|------|
| TLS 握手失败 | `listen 443` 漏了 `ssl`，或该 vhost 没配证书 |
| 502 | 容器没起来 → `docker compose logs` |
| 404 且无 `X-Powered-By` | 落到 nginx 默认 server，`server_name` 没匹配上 |
| 响应头 `X-Powered-By: authentik` | 请求打到了 authentik，域名或 `proxy_pass` 配错 |

## 升级与运维

```bash
docker compose pull && docker compose up -d    # 升级到最新镜像
docker compose logs -f --tail 50               # 看日志
docker compose down                            # 停止（keys 卷保留）
```

RSA 签名私钥存在名为 `unilink-keys` 的 named volume 里，容器重建不丢。
**不要删这个卷** —— 删了等于换发卡机构，authentik 缓存的公钥会验签失败。

备份：

```bash
docker run --rm -v unilink-auth_unilink-keys:/k -v "$PWD":/b alpine \
    tar czf /b/unilink-keys-backup.tar.gz -C /k .
```

## 与 authentik 同网络（可选）

如果 authentik 也在 Docker 里，可以让两者走容器名直连，省掉出公网绕一圈：

```bash
docker network ls          # 找到 authentik 的网络名，通常是 authentik_default
```

然后取消 `docker-compose.yml` 末尾 `networks` 段的注释、填上实际网络名，
并把 `.env` 里的 `UNILINK_AUTHENTIK_URL` 改成 `http://authentik-server:9000`。

注意 `UNILINK_BASE_URL` 仍必须是**对外的 https 地址** —— 它会写进 OIDC
发现文档，浏览器要按这个地址访问。

## 下一步

镜像跑起来只是第一步，还需要在 authentik 里做两处配置（纯 Web 界面）：
加 OAuth Source、给手机 App 建 OAuth2 Provider。
见 [docs/QR-LOGIN.md](../docs/QR-LOGIN.md) 的第二、三步。

## 自己构建镜像

不想用预构建镜像，或改过代码：

```bash
git clone https://github.com/Guyao146/unilink.git
cd unilink/auth-server
docker compose up -d --build      # 该目录下另有一份用于本地构建的 compose
```
