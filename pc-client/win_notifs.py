# -*- coding: utf-8 -*-
"""
捕获 Windows 系统通知（状态栏 Toast）并转发 —— 可选功能。

安装（二选一）:
  A. Python 3.7 ~ 3.11:   pip install winsdk==1.0.0b10
  B. Python 3.12+（微软新官方投影）:
       pip install winrt-runtime winrt-Windows.Foundation ^
                   winrt-Windows.UI.Notifications winrt-Windows.UI.Notifications.Management

授权:  Windows 设置 → 隐私和安全性 → 通知 → 允许应用访问通知
       （部分系统版本下未打包的应用会被拒绝授权，届时功能自动停用，
        不影响其它功能；手机→电脑方向不受影响。）
"""
import asyncio
import sys
import threading
import time

SUPPORTED = (sys.platform == "win32")


def _get_listener(mgmt):
    """获取 UserNotificationListener 单例实例。

    该 WinRT 类没有公开构造函数（官方用法是静态属性 Current），
    不同 Python 投影的暴露方式不一致：
      - 新版 winrt-*：类属性 .current（个别版本包装为可调用）
      - 旧版 winsdk ：类属性 .current，或允许直接构造
    逐个尝试，全部失败返回 None。
    """
    cls = mgmt.UserNotificationListener
    for attr in ("current", "Current"):
        try:
            v = getattr(cls, attr)
        except Exception:
            continue
        try:
            return v() if callable(v) else v
        except Exception:
            continue
    try:
        return cls()          # 兜底：允许直接构造的投影
    except Exception:
        return None


def _status_name(status) -> str:
    """把枚举/字符串统一成大写短名，便于展示与比较"""
    s = str(getattr(status, "name", status))
    return s.split(".")[-1].upper().strip("'\"")


def _is_allowed(status) -> bool:
    """是否已授权。官方成员名为 Allowed；部分实现写作 Granted——按关键字兼容"""
    s = _status_name(status)
    return ("ALLOWED" in s) or ("GRANTED" in s)


class WinNotifWatcher:
    """轮询 UserNotificationListener，把新出现的 Toast 通知推入 out_q"""

    def __init__(self, out_q):
        self.out_q = out_q
        self._stop = threading.Event()

    def start(self):
        if not SUPPORTED:
            return False
        threading.Thread(target=self._run, daemon=True,
                         name="unilink-notifwatch").start()
        return True

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            self.out_q.put({"type": "_sys_notify_error",
                            "error": getattr(e, "message", None) or str(e)})

    async def _main(self):
        # 兼容两套 WinRT 投影：新版微软官方 winrt-* / 旧版社区 winsdk
        try:
            from winrt.windows.ui.notifications import (
                NotificationKinds, KnownNotificationBindings)
            import winrt.windows.ui.notifications.management as mgmt
        except Exception:
            try:
                from winsdk.windows.ui.notifications import (
                    NotificationKinds, KnownNotificationBindings)
                import winsdk.windows.ui.notifications.management as mgmt
            except Exception as e:
                self.out_q.put({"type": "_sys_notify_error",
                                "error": "未安装通知捕获依赖: %s。"
                                         "Python 3.7~3.11 请执行 "
                                         "pip install winsdk==1.0.0b10；"
                                         "3.12+ 请见 requirements.txt 中的 "
                                         "winrt-* 安装说明" % e})
                return

        try:
            listener = _get_listener(mgmt)
            if listener is None:
                self.out_q.put({"type": "_sys_notify_error",
                                "error": "无法获取 UserNotificationListener 实例："
                                         "当前 WinRT 投影不支持任何已知获取方式"})
                return

            # 申请访问权限。注意两点：
            #  1) 官方枚举成员是 Allowed / Denied / Unspecified（不是 Granted），
            #     统一用 _is_allowed 按关键字判断；
            #  2) 观测到个别投影会把状态值以异常形式抛出（如裸文本 "GRANTED"，
            #     实际表示已授权），此处识别后视为成功。
            try:
                status = await listener.request_access_async()
            except Exception as e:
                if _is_allowed(str(e)):
                    status = "GRANTED"      # 已授权被误抛为异常 → 视为成功
                else:
                    raise

            if not _is_allowed(status):
                self.out_q.put({"type": "_sys_notify_error",
                                "error": "未获得“通知访问”权限（%s）："
                                         "请打开 设置 → 隐私和安全性 → 通知 "
                                         "允许访问，然后重新勾选本开关"
                                         % _status_name(status)})
                return
        except Exception as e:
            msg = str(e)
            extra = ""
            if "not activatable" in msg.lower():
                # 单例获取方式均已尝试仍失败：通常是系统要求包身份(MSIX)
                extra = ("。该 API 在部分 Windows 版本上要求应用具有包标识(MSIX)，"
                         "未打包的 Python 脚本会被系统拒绝——此单向功能不可用，"
                         "其余功能不受影响")
            self.out_q.put({"type": "_sys_notify_error",
                            "error": "初始化通知监听失败：%s%s" % (msg, extra)})
            return

        self.out_q.put({"type": "_sys_notify_ok"})
        seen = set()
        first = True

        while not self._stop.is_set():
            try:
                items = await listener.get_notifications_async(
                    NotificationKinds.TOAST) or []
            except Exception:
                items = []

            fresh = []
            for n in items:
                try:
                    nid = n.id
                except Exception:
                    continue
                if nid in seen:
                    continue
                seen.add(nid)
                fresh.append(n)
            if len(seen) > 3000:
                seen = set(list(seen)[-1500:])

            if not first:  # 首轮只做基线记录，不回放历史通知
                for n in fresh[:10]:
                    title, body = "", ""
                    ts = int(time.time() * 1000)
                    try:
                        b = n.notification.visual.get_binding(
                            KnownNotificationBindings.toast_generic())
                        if b is not None:
                            texts = list(b.get_text_elements())
                            if texts:
                                title = texts[0]
                            if len(texts) > 1:
                                body = "\n".join(texts[1:])
                    except Exception:
                        pass
                    try:
                        ts = int(n.creation_time.timestamp() * 1000)
                    except Exception:
                        pass
                    self.out_q.put({
                        "type": "_sys_notify",
                        "app": "Windows", "package": "windows",
                        "title": title, "body": body, "time": ts,
                    })
            first = False

            for _ in range(20):  # 共 2 秒，期间可快速响应 stop
                if self._stop.is_set():
                    return
                await asyncio.sleep(0.1)
