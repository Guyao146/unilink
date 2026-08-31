# UniLink 通信协议 v1

传输层：WebSocket（文本帧，UTF-8 JSON）。
服务器地址形如 `ws://host:port/ws`；服务器只做转发与成员管理，不解析业务内容。

---

## 1. 握手

客户端连接成功后**第一帧必须**是：

```json
{
  "type": "hello",
  "room": "88888888",          // 房间码：4-32 位 字母/数字/-/_
  "token": "口令",              // 服务器开启 --token 时必填
  "name": "我的电脑",           // 设备显示名
  "platform": "windows",       // windows / android / web ...
  "crypto_cap": true           // 本端是否具备 AES-GCM 加密能力
}
```

服务器回复：

```json
{ "type": "welcome",
  "you": "ab12cd34",                       // 分配给本端的 ID
  "peers": [{"id":"..","name":"..","platform":".."}],   // 不含自己
  "crypto": true }                          // 房间最终是否启用加密
```

广播给其他人：`{"type":"peer_joined","peer":{...},"crypto":bool}`、
`{"type":"peer_left","peer_id":"..","crypto":bool}`。

## 2. 端到端加密协商

* 规则：房间内**所有**成员 `crypto_cap` 均为真时，`crypto=true`。
* 任一成员加入/离开导致结果变化时，服务器广播：
  `{"type":"crypto_changed","crypto":bool}`
* 客户端以最近的 `welcome.crypto / crypto_changed.crypto` 为准决定加解密。

## 3. 业务消息信封

```json
{
  "type": "msg",
  "id":   "0f3a92c1b7d4",            // 消息 ID（去横线 UUID 前 12 位）
  "ts":   1730000000000,             // 毫秒时间戳
  "from": {"id":"..","name":"手机","platform":"android"},  // 服务器覆写，防伪造
  "to":   "all",                     // "all" 或目标 peer id（私发）
  "kind": "text",                    // 见下表
  "enc":  false,
  "payload":     {...},              // enc=false 时
  "payload_enc": "base64..."         // enc=true 时
}
```

| kind | payload 字段 | 说明 |
|------|--------------|------|
| `text` | `text` | 文字消息 |
| `notify` | `app`,`package`,`title`,`body`,`time` | 状态栏通知镜像 |
| `clipboard` | `text` | 剪贴板同步 |
| `file-meta` | `fid`,`name`,`size`,`mime`,`chunks` | 开始传文件 |
| `file-chunk` | `fid`,`i`,`data`(base64) | 文件分块，每块 ≤512KB |
| `file-end` | `fid` | 传输结束 |
| `notify-action` | `act`,`rid`,`key`,`pkg`,`app`,`title`,`text` | 电脑请求手机回复某条通知（定向发送） |
| `notify-action-ack` | `act:"reply"`,`rid`,`ok`,`msg` | 手机回报回复执行结果 |
| `capability` | `platform`,`a11y`,`modes[]` | 手机广播能力表：`modes=[{pkg,label,open_body},...]` 为适配规则，`a11y` 为无障碍开关状态 |

> **capability 触发时机**：收到 welcome 后、有新设备加入时（补发给错过广播的 PC）、
> 无障碍服务连接/断开时。PC 缓存 per-peer 能力表；未收到 capability 帧的 PC
> 可按内置默认表推断模式。

## 3.1 通知回复流程

```
PC                                Android
│  notify-action(reply) ────────► │
│   to=来源手机, rid, key, text    │ 屏幕已解锁且无障碍服务在线？
│                                 │  ├─ 是：拉通知栏→点“回复”→填文本→点“发送”
│                                 │  └─ 否：复制到剪贴板+打开来App，用户手动粘贴
│ ◄─────────── notify-action-ack  │
│   rid 匹配, ok=true/false, msg  │
```

## 4. 加密算法（PC 与 Android 一致）

```
key     = PBKDF2-HMAC-SHA256( password = token,
                              salt     = "unilink|" + room,
                              iter     = 120000, dkLen = 32 )
密文blob = base64( IV[12字节随机] ‖ AES-256-GCM(payloadJSON).ciphertext ‖ tag[16] )
```

* Python: `hashlib.pbkdf2_hmac` + `cryptography.AESGCM`
* Android: `PBKDF2WithHmacSHA256` + `AES/GCM/NoPadding`

## 5. 安全说明

* 服务器开启 `--token` 后校验 hello 令牌，且令牌参与密钥派生 —— **令牌即密码，请使用强口令**。
* 公网部署建议用 Nginx/Caddy 反向代理升级为 `wss://`（TLS），防止流量特征分析。
* 明文模式下（如浏览器测试页加入），消息仅受 TLS 保护，请注意环境安全。
