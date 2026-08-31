#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UniLink PC 端 —— 手机 ⇄ 电脑 互联助手
=====================================
功能:
  1. 与手机互发文字消息（端到端加密）
  2. 手机状态栏通知 → 电脑桌面弹窗 + 日志
  3. 电脑系统通知（Toast）→ 手机状态栏（需 winsdk + 系统授权）
  4. 剪贴板双向同步
  5. 文件互传（接收目录：~/Downloads/UniLink）

运行:
  pip install -r requirements.txt
  python main.py
"""
import base64
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from net import NetClient
from cryptobox import HAVE_CRYPTO, CryptoBox
import proto
import toasts

try:
    import win_notifs
except Exception:
    win_notifs = None

IS_WIN = (sys.platform == "win32")
CHUNK = 512 * 1024  # 512KB / 块
# PyInstaller 打包成 exe 后 __file__ 指向临时解压目录，
# 配置文件必须跟随 exe 所在目录，否则每次启动配置都会丢失
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(_APP_DIR, "config.json")

# 与手机端 AppRules.kt 默认表保持一致；
# 手机在线后会通过 capability 帧动态同步真实规则表，此表仅作离线兜底。
DEFAULT_MODES = {
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.tencent.tim": "QQ",
    "com.tencent.qqlite": "QQ",
    "org.telegram.messenger": "Telegram",
    "org.telegram.messenger.web": "Telegram",
    "org.telegram.plus": "Telegram",
    "nekox.messenger": "Telegram",
    "tw.nekomimi.nekogram": "Telegram",
}


def fmt_size(n: int) -> str:
    f = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if f < 1024 or u == "GB":
            return ("%.0f%s" if u == "B" else "%.1f%s") % (f, u)
        f /= 1024
    return "%dB" % n


def res_dir() -> str:
    base = os.path.join(os.path.expanduser("~"), "Downloads", "UniLink")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        fallback = os.path.join(os.path.expanduser("~"), "UniLink")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def safe_name(n: str) -> str:
    n = os.path.basename(n or "file")
    n = re.sub(r'[\\/:*?"<>|\r\n]', "_", n).strip()
    return (n[:120]) or "file"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UniLink —— 手机 ⇄ 电脑 互联助手")
        self.geometry("760x600")
        self.minsize(600, 460)

        # 运行状态
        self.net: NetClient = None
        self.crypto_box: CryptoBox = None
        self.crypto_on = False
        self.my_id = ""
        self.platform = "windows" if IS_WIN else sys.platform
        self.my_name = "我的电脑"
        self.peers = {}
        self.q = queue.Queue()
        self.files = {}          # fid -> 接收中的文件
        self.watcher = None      # Windows 通知监听
        self.clip_last = None
        self.clip_suppress = None
        self.toasts = []
        self.recent_notifs = deque(maxlen=30)   # 最近收到的手机通知（供回复选择）
        self.pending_replies = {}               # rid -> {title, app} 等待 ACK
        self.peer_caps = {}                     # peer_id -> capability payload（适配模式表）

        self._load_cfg()
        self._build_ui()
        self.after(80, self._poll)
        self.after(1600, self._clip_tick)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ================= 配置 =================

    def _load_cfg(self):
        d = {"server": "ws://127.0.0.1:8765/ws", "room": "88888888",
             "token": "", "name": "我的电脑",
             "notify": True, "clip": False, "toast": True}
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                d.update(json.load(f))
        except Exception:
            pass
        self.cfg = d

    def _save_cfg(self):
        d = {"server": self.v_srv.get().strip(),
             "room": self.v_room.get().strip(),
             "token": self.v_token.get(),
             "name": self.v_name.get().strip() or "我的电脑",
             "notify": self.v_notify.get(),
             "clip": self.v_clip.get(),
             "toast": self.v_toast.get()}
        try:
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ================= 界面 =================

    def _build_ui(self):
        pad = dict(padx=6, pady=4)

        top = ttk.LabelFrame(self, text="① 连接设置")
        top.pack(fill="x", **pad)
        self.v_srv = tk.StringVar(value=self.cfg["server"])
        self.v_room = tk.StringVar(value=self.cfg["room"])
        self.v_token = tk.StringVar(value=self.cfg["token"])
        self.v_name = tk.StringVar(value=self.cfg["name"])
        ttk.Label(top, text="服务器").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.v_srv, width=30).grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(top, text="房间码").grid(row=0, column=2, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.v_room, width=12).grid(row=0, column=3, **pad)
        ttk.Label(top, text="令牌").grid(row=0, column=4, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.v_token, width=14, show="•").grid(row=0, column=5, **pad)
        ttk.Label(top, text="设备名").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.v_name, width=14).grid(row=1, column=1, sticky="w", **pad)
        self.btn_conn = ttk.Button(top, text="连接", command=self._connect)
        self.btn_conn.grid(row=1, column=2, **pad)
        self.btn_disc = ttk.Button(top, text="断开", command=self._disconnect, state="disabled")
        self.btn_disc.grid(row=1, column=3, **pad)
        ttk.Button(top, text="打开接收目录", command=self._open_dir).grid(row=1, column=4, columnspan=2, **pad)
        top.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(self, text="② 同步选项")
        opt.pack(fill="x", **pad)
        self.v_notify = tk.BooleanVar(value=self.cfg["notify"])
        self.v_clip = tk.BooleanVar(value=self.cfg["clip"])
        self.v_toast = tk.BooleanVar(value=self.cfg["toast"])
        cbn = ttk.Checkbutton(opt, text="转发本机系统通知 → 手机",
                              variable=self.v_notify, command=self._toggle_notify)
        cbn.pack(side="left", **pad)
        if not IS_WIN or win_notifs is None:
            cbn.state(["disabled"])
        ttk.Checkbutton(opt, text="剪贴板自动同步",
                        variable=self.v_clip).pack(side="left", **pad)
        ttk.Checkbutton(opt, text="桌面弹窗提醒",
                        variable=self.v_toast).pack(side="left", **pad)
        self.lb_info = ttk.Label(opt, text="未连接", foreground="#666")
        self.lb_info.pack(side="right", **pad)

        mid = ttk.LabelFrame(self, text="③ 消息与事件")
        mid.pack(fill="both", expand=True, **pad)
        self.txt = scrolledtext.ScrolledText(mid, height=18, state="disabled",
                                             font=("Microsoft YaHei", 10), wrap="word")
        self.txt.pack(fill="both", expand=True, **pad)
        for tag, color in (("sys", "#5b6b7a"), ("in", "#0b5cad"),
                           ("out", "#1a7f37"), ("err", "#c62828"),
                           ("file", "#7b1fa2")):
            self.txt.tag_configure(tag, foreground=color)

        bot = ttk.Frame(self)
        bot.pack(fill="x", **pad)
        self.v_msg = tk.StringVar()
        self.ent = ttk.Entry(bot, textvariable=self.v_msg)
        self.ent.pack(side="left", fill="x", expand=True, padx=(6, 4))
        self.ent.bind("<Return>", lambda e: self._send_text())
        ttk.Button(bot, text="发送", command=self._send_text).pack(side="left", padx=2)
        ttk.Button(bot, text="发文件…", command=self._pick_file).pack(side="left", padx=2)
        ttk.Button(bot, text="回复通知…", command=self._open_reply_dialog).pack(side="left", padx=2)
        ttk.Button(bot, text="发剪贴板", command=self._send_clip_now).pack(side="left", padx=2)

        self.lb_status = ttk.Label(self, text="● 未连接", anchor="w", relief="sunken")
        self.lb_status.pack(fill="x", side="bottom")

    # ================= 日志 / 状态 =================

    def _log(self, text: str, tag="sys"):
        ts = time.strftime("%H:%M:%S")
        self.txt.configure(state="normal")
        self.txt.insert("end", "[%s] %s\n" % (ts, text), tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _set_status(self, text, color="#555"):
        self.lb_status.configure(text="● " + text, foreground=color)

    def _refresh_info(self):
        if not (self.net and self.net.connected):
            self.lb_info.configure(text="未连接")
            return
        enc = "🔐 AES-GCM" if self.crypto_on else "⚠ 明文"
        self.lb_info.configure(text="在线设备 %d 台 · %s" % (len(self.peers) + 1, enc))

    # ================= 连接管理 =================

    def _connect(self):
        srv = self.v_srv.get().strip() or "ws://127.0.0.1:8765/ws"
        room = self.v_room.get().strip()
        if not re.match(r"^[\w\-]{4,32}$", room):
            messagebox.showwarning("UniLink", "房间码需为 4-32 位字母/数字/-/_")
            return
        self._save_cfg()
        self.my_name = self.v_name.get().strip() or "我的电脑"
        self.crypto_box = CryptoBox(room, self.v_token.get()) if HAVE_CRYPTO else None

        hello = {"type": "hello", "room": room, "token": self.v_token.get(),
                 "name": self.my_name, "platform": self.platform,
                 "crypto_cap": HAVE_CRYPTO}
        if self.net:
            self.net.stop()
        self.net = NetClient(srv, hello, self.q.put)
        self.net.start()
        self.btn_conn.configure(state="disabled")
        self.btn_disc.configure(state="normal")
        self._set_status("连接中…", "#b58900")
        self._log("正在连接 %s …" % srv, "sys")
        if not HAVE_CRYPTO:
            self._log("未安装 cryptography，将使用明文模式（建议 pip install cryptography）", "err")
        if self.v_notify.get():
            self._start_watcher()

    def _disconnect(self):
        if self.net:
            self.net.stop()
            self.net = None
        self._stop_watcher(silent=True)
        self.btn_conn.configure(state="normal")
        self.btn_disc.configure(state="disabled")
        self._set_status("未连接", "#555")
        self._log("已断开", "sys")

    def _open_dir(self):
        path = res_dir()
        try:
            if IS_WIN:
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                os.system('open "%s"' % path)
            else:
                os.system('xdg-open "%s" &' % path)
        except Exception as e:
            messagebox.showerror("UniLink", str(e))

    # ================= 事件轮询（主线程） =================

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                try:
                    self._handle(msg)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, m: dict):
        t = m.get("type")

        if t == "_net":
            st = m.get("state")
            if st == "connected":
                self._set_status("已连接", "#1a7f37")
            elif st == "reconnecting":
                self._set_status("重连中（%ss 后重试）" % m.get("delay", 1), "#b58900")
                self.peers = {}
                self._refresh_info()
            elif st == "error":
                self._set_status("连接错误: %s" % str(m.get("error", ""))[:70], "#c62828")
            return

        if t == "welcome":
            self.my_id = m.get("you", "")
            self.crypto_on = bool(m.get("crypto"))
            self.peers = {p["id"]: p for p in m.get("peers", [])}
            names = "、".join(p.get("name", "?") for p in self.peers.values()) or "无"
            self._log("✅ 已加入房间（加密: %s）。在线设备：%s"
                      % ("AES-GCM" if self.crypto_on else "明文", names), "sys")
            self._refresh_info()

        elif t == "peer_joined":
            p = m.get("peer", {})
            self.peers[p.get("id")] = p
            self._log("📱 设备上线：%s（%s）" % (p.get("name"), p.get("platform")), "sys")
            self._refresh_info()

        elif t == "peer_left":
            p = self.peers.pop(m.get("peer_id"), None)
            self.peer_caps.pop(m.get("peer_id"), None)
            if p:
                self._log("设备离线：%s" % p.get("name"), "sys")
            self._refresh_info()

        elif t == "crypto_changed":
            self.crypto_on = bool(m.get("crypto"))
            self._log("房间加密模式切换为：%s"
                      % ("AES-GCM 加密" if self.crypto_on else "明文"), "sys")
            self._refresh_info()

        elif t == "error":
            self._log("服务器: " + str(m.get("message", "")), "err")

        elif t == "_sys_notify_ok":
            self._log("已开始捕获本机系统通知并转发给手机", "sys")

        elif t == "_sys_notify_error":
            self._log("⚠ 通知捕获已停止：%s" % m.get("error", ""), "err")

        elif t == "_sys_notify":
            self._out_notify(m)

        elif t == "msg":
            self._on_msg(m)

    # ================= 收消息 =================

    def _on_msg(self, env: dict):
        frm = env.get("from") or {}
        if frm.get("id") == self.my_id:
            return
        fname = frm.get("name", "?")
        payload, err = proto.decrypt_payload(env, self.crypto_box if self.crypto_on else None)
        if err:
            self._log(err, "err")
            return
        kind = env.get("kind")

        if kind == proto.KIND_TEXT:
            txt = str(payload.get("text", ""))
            self._log("💬 %s：%s" % (fname, txt), "in")
            if self.v_toast.get():
                toasts.show("💬 %s 发来消息" % fname, txt, hook=self._tk_toast)

        elif kind == proto.KIND_NOTIFY:
            app = str(payload.get("app", ""))
            ti = str(payload.get("title", ""))
            bo = str(payload.get("body", ""))
            # 记录到“可回复”列表（带原通知 key 与来源设备，供定向回复）
            self.recent_notifs.append({
                "ts": int(payload.get("time") or (time.time() * 1000)),
                "from": frm.get("id", "all"),
                "key": str(payload.get("key", "")),
                "pkg": str(payload.get("package", "")),
                "app": app, "title": ti, "body": bo,
            })
            self._log("🔔 [%s · %s] %s | %s" % (fname, app, ti, bo.replace("\n", " / ")), "in")
            if self.v_toast.get():
                toasts.show("🔔 %s · %s" % (fname, app or "通知"),
                            (ti + "\n" + bo).strip(), hook=self._tk_toast)

        elif kind == proto.KIND_NOTIFY_ACK:
            rid = str(payload.get("rid", ""))
            ok = bool(payload.get("ok"))
            msg = str(payload.get("msg", ""))
            info = self.pending_replies.pop(rid, None)
            tgt = ("『%s』" % info["title"]) if info else ""
            if ok:
                self._log("✅ 手机%s回复已发送%s" % (tgt, ("：" + msg) if msg else ""), "out")
            else:
                self._log("⚠ %s回复未自动完成：%s" % (tgt, msg), "err")

        elif kind == proto.KIND_CAPABILITY:
            # 手机端能力表：适配规则 + 无障碍状态（回复弹窗据此显示适配模式）
            self.peer_caps[frm.get("id", "")] = payload
            self._log("📱 %s 能力同步：无障碍=%s，专属适配 %d 个应用"
                      % (fname, "开" if payload.get("a11y") else "关",
                         len(payload.get("modes", []))), "sys")

        elif kind == proto.KIND_CLIPBOARD:
            txt = str(payload.get("text", ""))
            h = hash(txt)
            if self.clip_suppress == h:
                self.clip_suppress = None
                self._log("（剪贴板回环已忽略）", "sys")
            else:
                try:
                    self.clipboard_clear()
                    self.clipboard_append(txt)
                    self.clip_last = txt
                    self._log("📋 已同步 %s 的剪贴板（%d 字）" % (fname, len(txt)), "in")
                except Exception:
                    pass

        elif kind == proto.KIND_FILE_META:
            fid = str(payload.get("fid", ""))
            self.files[fid] = {"name": safe_name(str(payload.get("name", "file"))),
                               "size": int(payload.get("size", 0)),
                               "total": int(payload.get("chunks", 0)) or 1,
                               "buf": bytearray(), "got": 0, "last_pct": -1}
            self._log("📥 %s 发来文件：%s（%s）"
                      % (fname, self.files[fid]["name"], fmt_size(self.files[fid]["size"])), "file")

        elif kind == proto.KIND_FILE_CHUNK:
            f = self.files.get(str(payload.get("fid", "")))
            if not f:
                return
            try:
                f["buf"] += base64.b64decode(str(payload.get("data", "")))
            except Exception:
                return
            f["got"] += 1
            pct = f["got"] * 100 // f["total"]
            if pct != f["last_pct"] and pct % 20 == 0:
                f["last_pct"] = pct
                self._log("   ↓ %s %d%%" % (f["name"], pct), "file")

        elif kind == proto.KIND_FILE_END:
            f = self.files.pop(str(payload.get("fid", "")), None)
            if not f:
                return
            stem, ext = os.path.splitext(f["name"])
            path = os.path.join(res_dir(), f["name"])
            i = 1
            while os.path.exists(path):
                path = os.path.join(res_dir(), "%s(%d)%s" % (stem, i, ext))
                i += 1
            try:
                with open(path, "wb") as fp:
                    fp.write(bytes(f["buf"]))
                self._log("✅ 文件已保存：%s" % path, "file")
                if self.v_toast.get():
                    toasts.show("文件接收完成", path, hook=self._tk_toast)
            except Exception as e:
                self._log("保存文件失败：%s" % e, "err")

    # ================= 发送 =================

    def _box(self):
        return self.crypto_box if (self.crypto_on and self.crypto_box) else None

    def _require_conn(self) -> bool:
        if not (self.net and self.net.connected):
            messagebox.showinfo("UniLink", "尚未连接，请先连接服务器")
            return False
        return True

    def _send_text(self):
        v = self.v_msg.get().strip()
        if not v:
            return
        if not self._require_conn():
            return
        env = proto.envelope(self.my_id, self.my_name, self.platform,
                             proto.KIND_TEXT, {"text": v}, self._box())
        self.net.send(env)
        self._log("💬 我：%s" % v, "out")
        self.v_msg.set("")

    def _send_clip_now(self):
        if not self._require_conn():
            return
        try:
            cur = self.clipboard_get()
        except Exception:
            cur = ""
        if not cur:
            messagebox.showinfo("UniLink", "剪贴板为空或不是文本")
            return
        env = proto.envelope(self.my_id, self.my_name, self.platform,
                             proto.KIND_CLIPBOARD, {"text": cur[:100000]}, self._box())
        self.net.send(env)
        self._log("📋 ↑ 已发送剪贴板（%d 字）" % len(cur), "out")

    def _out_notify(self, d: dict):
        if not (self.net and self.net.connected):
            return
        payload = {"app": d.get("app", "PC"), "package": d.get("package", ""),
                   "title": d.get("title", ""), "body": d.get("body", ""),
                   "time": int(time.time() * 1000)}
        env = proto.envelope(self.my_id, self.my_name, self.platform,
                             proto.KIND_NOTIFY, payload, self._box())
        self.net.send(env)
        self._log("🔔 ↑ 本机通知 [%s] %s | %s"
                  % (payload["app"], payload["title"],
                     payload["body"].replace("\n", " / ")), "out")

    # ================= 回复手机通知 =================

    def mode_for(self, peer_id, pkg):
        """返回该通知在手机端将使用的适配模式名称（如「微信模式」）。
        优先用手机动态同步的能力表，未同步时退回本地默认表。"""
        label = None
        caps = self.peer_caps.get(peer_id)
        if caps is not None:
            for m in caps.get("modes", []):
                if m.get("pkg") == pkg:
                    label = m.get("label") or ""
                    break
            if label is None:
                label = ""          # 手机明确没有该包的专属规则 → 通用模式
        else:
            label = DEFAULT_MODES.get(pkg or "", "")
        return (label + "模式") if label else "通用模式"

    def _open_reply_dialog(self):
        if not self.recent_notifs:
            messagebox.showinfo("UniLink", "还没有收到过手机通知")
            return
        if not self._require_conn():
            return
        win = tk.Toplevel(self)
        win.title("回复手机通知")
        win.geometry("540x470")
        win.transient(self)

        ttk.Label(win, text="① 选择要回复的通知（最近 30 条，双击直接定位输入框）：").pack(
            anchor="w", padx=8, pady=(8, 2))
        lb = tk.Listbox(win, height=9, activestyle="dotbox",
                        font=("Microsoft YaHei", 9))
        lb.pack(fill="x", padx=8)
        items = list(self.recent_notifs)[::-1]      # 最新在最上
        for i, n in enumerate(items):
            ts = time.strftime("%H:%M", time.localtime(n["ts"] / 1000))
            summary = (n["title"] + " — " + n["body"]).replace("\n", " ")[:40]
            mode = self.mode_for(n.get("from"), n.get("pkg"))
            badge = "" if mode == "通用模式" else " ⟨%s⟩" % mode
            lb.insert("end", " %d. %s · %s · %s%s"
                      % (i + 1, ts, n["app"] or "?", summary, badge))
        lb.selection_set(0)

        # 适配模式说明行：显示手机端将采用的回复方式与降级警告
        lb_detail = ttk.Label(win, text="", foreground="#555", wraplength=510)
        lb_detail.pack(fill="x", padx=8, pady=(4, 0))

        def update_detail(_e=None):
            sel = lb.curselection()
            if not sel:
                lb_detail.configure(text="")
                return
            n = items[sel[0]]
            mode = self.mode_for(n.get("from"), n.get("pkg"))
            caps = self.peer_caps.get(n.get("from"))
            if caps is None:
                extra = "（未收到该手机的能力信息，按默认规则推断）"
            elif not caps.get("a11y"):
                extra = " ⚠ 手机未开启无障碍，将退化为「复制+打开App」"
            else:
                m = next((x for x in caps.get("modes", [])
                          if x.get("pkg") == n.get("pkg")), None)
                extra = "（将点开通知进入会话后填写）" \
                    if (m and m.get("open_body")) else ""
            lb_detail.configure(text="▸ 手机将使用「%s」自动回复%s" % (mode, extra))

        lb.bind("<<ListboxSelect>>", update_detail)
        update_detail()

        ttk.Label(win, text="② 输入回复内容：").pack(anchor="w", padx=8, pady=(8, 2))
        txt = tk.Text(win, height=5, font=("Microsoft YaHei", 10))
        txt.pack(fill="both", expand=True, padx=8)
        txt.focus_set()

        def on_dbl(_e):
            txt.focus_set()
        lb.bind("<Double-Button-1>", on_dbl)

        def do_send():
            sel = lb.curselection()
            if not sel:
                messagebox.showwarning("UniLink", "请先选择一条通知", parent=win)
                return
            text = txt.get("1.0", "end").strip()
            if not text:
                messagebox.showwarning("UniLink", "请输入回复内容", parent=win)
                return
            n = items[sel[0]]
            win.destroy()
            self._send_reply(n, text)

        bf = ttk.Frame(win)
        bf.pack(fill="x", padx=8, pady=8)
        ttk.Button(bf, text="发送到手机", command=do_send).pack(side="right")
        ttk.Button(bf, text="取消", command=win.destroy).pack(side="right", padx=6)
        win.bind("<Control-Return>", lambda e: do_send())

    def _send_reply(self, n: dict, text: str):
        if not self._require_conn():
            return
        rid = proto.new_id()
        self.pending_replies[rid] = {"title": n["title"], "app": n["app"]}
        payload = {"act": "reply", "rid": rid,
                   "key": n.get("key", ""), "pkg": n.get("pkg", ""),
                   "app": n.get("app", ""), "title": n.get("title", ""),
                   "text": text[:500]}
        env = proto.envelope(self.my_id, self.my_name, self.platform,
                             proto.KIND_NOTIFY_ACTION, payload, self._box())
        env["to"] = n.get("from") or "all"      # 定向发给该通知来源的手机
        self.net.send(env)
        mode = self.mode_for(n.get("from"), n.get("pkg"))
        self._log("⌨ 已请求手机回复『%s』〔%s〕：%s"
                  % (n["title"], mode, text.replace("\n", " / ")), "out")

    def _pick_file(self):
        if not self._require_conn():
            return
        path = filedialog.askopenfilename(title="选择要发送的文件")
        if path:
            threading.Thread(target=self._do_send_file, args=(path,),
                             daemon=True).start()

    def _do_send_file(self, path: str):
        try:
            size = os.path.getsize(path)
            if size > 2 * 1024 ** 3:
                self.q.put({"type": "error", "message": "文件超过 2GB，暂不支持"})
                return
            fid = proto.new_id()
            name = safe_name(os.path.basename(path))
            chunks = (size + CHUNK - 1) // CHUNK
            box = self._box()
            mk = lambda kind, pl: proto.envelope(self.my_id, self.my_name,
                                                 self.platform, kind, pl, box)
            self.net.send(mk(proto.KIND_FILE_META,
                             {"fid": fid, "name": name, "size": size,
                              "mime": "application/octet-stream", "chunks": chunks}))
            self._log("📤 发送文件 %s（%d 块）" % (name, chunks), "file")
            with open(path, "rb") as fp:
                i = 0
                while True:
                    data = fp.read(CHUNK)
                    if not data:
                        break
                    self.net.send(mk(proto.KIND_FILE_CHUNK,
                                     {"fid": fid, "i": i,
                                      "data": base64.b64encode(data).decode("ascii")}))
                    i += 1
                    time.sleep(0.01)  # 让出网络线程
            self.net.send(mk(proto.KIND_FILE_END, {"fid": fid}))
            self._log("✅ 文件发送完成：%s" % name, "file")
        except Exception as e:
            self._log("发送文件失败：%s" % e, "err")

    # ================= 剪贴板监听（主线程定时器） =================

    def _clip_tick(self):
        self.after(1600, self._clip_tick)
        if not (self.v_clip.get() and self.net and self.net.connected):
            return
        try:
            cur = self.clipboard_get()
        except Exception:
            return
        if not isinstance(cur, str) or not cur:
            return
        if self.clip_last is None:      # 首次只记录基线
            self.clip_last = cur
            return
        if cur != self.clip_last:
            self.clip_last = cur
            h = hash(cur)
            if self.clip_suppress == h:  # 远端刚设置的内容，不回发
                self.clip_suppress = None
                return
            env = proto.envelope(self.my_id, self.my_name, self.platform,
                                 proto.KIND_CLIPBOARD, {"text": cur[:100000]}, self._box())
            self.net.send(env)
            self._log("📋 ↑ 剪贴板变更已发送（%d 字）" % len(cur), "out")

    # ================= 本机通知监听 =================

    def _start_watcher(self):
        if not IS_WIN:
            return
        if win_notifs is None:
            self._log("提示：pip install winsdk==1.0.0b10（Python 3.7~3.11）"
                      "并在系统设置中授权后，才能把电脑自己的通知转发给手机", "sys")
            return
        if self.watcher is None:
            self.watcher = win_notifs.WinNotifWatcher(self.q)
            self.watcher.start()

    def _stop_watcher(self, silent=False):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            if not silent:
                self._log("已停止转发本机通知", "sys")

    def _toggle_notify(self):
        if self.v_notify.get():
            self._start_watcher()
        else:
            self._stop_watcher()

    # ================= Tk 兜底弹窗 =================

    def _tk_toast(self, title, body, duration=6):
        def make():
            try:
                w = tk.Toplevel(self)
                w.overrideredirect(True)
                w.attributes("-topmost", True)
                f = tk.Frame(w, bg="#1f2430", padx=14, pady=10)
                tk.Label(f, text=title, bg="#1f2430", fg="#ffffff",
                         font=("Microsoft YaHei", 10, "bold"),
                         wraplength=300, justify="left").pack(anchor="w")
                if body:
                    tk.Label(f, text=body, bg="#1f2430", fg="#d8dee9",
                             font=("Microsoft YaHei", 9),
                             wraplength=300, justify="left").pack(anchor="w")
                f.pack()
                w.update_idletasks()
                sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
                ww, wh = w.winfo_width(), w.winfo_height()
                x = sw - ww - 24
                y = sh - wh - 60 - (len(self.toasts) % 4) * 84
                w.geometry("+%d+%d" % (x, y))
                w.bind("<Button-1>", lambda e: w.destroy())
                w.after(duration * 1000, w.destroy)
                self.toasts.append(w)
                self.toasts = [t for t in self.toasts if t.winfo_exists()][-5:]
            except Exception:
                pass
        self.after(0, make)

    # ================= 退出 =================

    def _close(self):
        try:
            self._save_cfg()
            self._stop_watcher(silent=True)
            if self.net:
                self.net.stop()
        finally:
            self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
