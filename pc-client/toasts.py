# -*- coding: utf-8 -*-
"""
桌面通知（Windows 真实系统 Toast → plyer → Tk 兜底）
====================================================
优先级:
  1. Windows 系统 Toast（右下角弹出、进操作中心；MyDockFinder 等 dock
     软件不会拦截系统通知，可正常显示）
     - 复用已安装的 winsdk / winrt-* 投影，零新增依赖
     - 未打包应用发 Toast 需要一个带 AUMID 的开始菜单快捷方式，
       首次使用时本模块会通过 COM 自动创建（UniLink.lnk），之后静默复用
  2. plyer
  3. main 注入的 tkinter 弹窗 hook

所有通知都在后台线程触发，不阻塞网络/GUI。
"""
import os
import sys
import threading

_AUMID = "UniLink.Notifier"
_LNK_NAME = "UniLink.lnk"
_shortcut_done = False      # 本进程内是否已完成快捷方式引导


def show(title: str, body: str, hook=None, duration: int = 6):
    threading.Thread(target=_worker, args=(title, body, hook, duration),
                     daemon=True).start()


def _worker(title, body, hook, duration):
    title = (title or "UniLink")[:64]
    body = (body or " ")[:220]

    if sys.platform == "win32":
        try:
            _toast_winrt(title, body)
            return
        except Exception:
            pass
    try:
        from plyer import notification  # noqa
        notification.notify(title=title, message=body,
                            app_name="UniLink", timeout=duration)
        return
    except Exception:
        pass
    h = hook
    if h:
        try:
            h(title, body, duration)
        except Exception:
            pass


# ======================================================================
# Windows 真实系统 Toast
# ======================================================================

def _import_winrt_ns():
    """返回 notifications 命名空间模块与 XmlDocument 类（双投影兼容）"""
    try:
        import winsdk.windows.ui.notifications as wn
        from winsdk.windows.data.xml.dom import XmlDocument
        return wn, XmlDocument
    except ImportError:
        import winrt.windows.ui.notifications as wn
        from winrt.windows.data.xml.dom import XmlDocument
        return wn, XmlDocument


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;") \
                    .replace(">", "&gt;")


def _toast_winrt(title: str, body: str):
    wn, XmlDocument = _import_winrt_ns()
    xml = ('<toast activationType="system">'
           '<visual><binding template="ToastGeneric">'
           f'<text>{_esc(title)}</text>'
           f'<text>{_esc(body)}</text>'
           '</binding></visual></toast>')
    doc = XmlDocument()
    doc.load_xml(xml)
    toast = wn.ToastNotification(doc)

    try:
        notifier = wn.ToastNotificationManager.create_toast_notifier(_AUMID)
        notifier.show(toast)
        return
    except Exception:
        pass

    # 多数系统要求 AUMID 对应的开始菜单快捷方式存在 —— 创建后重试一次
    _ensure_shortcut()
    notifier = wn.ToastNotificationManager.create_toast_notifier(_AUMID)
    notifier.show(toast)


# ----------------------------------------------------------------------
# AUMID 快捷方式引导（ctypes 直接操作 ShellLink/IPropertyStore，无第三方依赖）
# ----------------------------------------------------------------------

def _shortcut_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    programs = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")
    return os.path.join(programs, _LNK_NAME)


def _marker_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "UniLink", "aumid.ok")


def _ensure_shortcut() -> bool:
    """创建带 System.AppUserModel.ID 属性的快捷方式；成功后写标记文件"""
    global _shortcut_done
    if _shortcut_done:
        return True

    lnk = _shortcut_path()
    marker = _marker_path()
    if os.path.exists(lnk) or os.path.exists(marker):
        _shortcut_done = True
        return True

    try:
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        _create_lnk_with_aumid(lnk, _AUMID, sys.executable)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("ok")
        _shortcut_done = True
        return True
    except Exception:
        return False


def _create_lnk_with_aumid(lnk_path: str, aumid: str, target_exe: str):
    import ctypes
    from ctypes import c_void_p, c_wchar_p
    from ctypes import wintypes

    ole32 = ctypes.windll.ole32          # windll 不自动抛 HRESULT 异常，手动检查
    propsys = ctypes.WinDLL("propsys")

    # ---- 结构体 ----
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    def make_guid(s: str) -> GUID:
        g = GUID()
        if ole32.CLSIDFromString(c_wchar_p(s), ctypes.byref(g)) != 0:
            raise RuntimeError("CLSIDFromString: " + s)
        return g

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort),
                    ("reserved", ctypes.c_ubyte * 6),
                    ("ptr", c_void_p)]

    class IFACE(ctypes.Structure):
        _fields_ = [("lpVtbl", ctypes.POINTER(c_void_p))]

    # ---- 常量 ----
    CLSID_ShellLink    = make_guid("{00021401-0000-0000-C000-000000000046}")
    IID_IShellLinkW    = make_guid("{000214F9-0000-0000-C000-000000000046}")
    IID_IPersistFile   = make_guid("{0000010B-0000-0000-C000-000000000046}")
    IID_IPropertyStore = make_guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    # System.AppUserModel.ID
    PKEY_AppUserModel_ID = PROPERTYKEY(
        fmtid=make_guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), pid=3)

    QIProto      = ctypes.WINFUNCTYPE(wintypes.HRESULT, c_void_p,
                                      ctypes.POINTER(GUID),
                                      ctypes.POINTER(c_void_p))
    SetPathProto = ctypes.WINFUNCTYPE(wintypes.HRESULT, c_void_p,
                                      wintypes.LPCWSTR)
    SaveProto    = ctypes.WINFUNCTYPE(wintypes.HRESULT, c_void_p,
                                      wintypes.LPCWSTR, wintypes.BOOL)
    SetValueProto = ctypes.WINFUNCTYPE(wintypes.HRESULT, c_void_p,
                                       ctypes.POINTER(PROPERTYKEY),
                                       ctypes.POINTER(PROPVARIANT))
    VoidProto    = ctypes.WINFUNCTYPE(wintypes.HRESULT, c_void_p)

    def vtbl_fn(ptr, index: int, proto):
        tbl = ctypes.cast(ptr.contents.lpVtbl, ctypes.POINTER(c_void_p))
        return ctypes.cast(tbl[index], proto)

    def release(ptr):
        try:
            vtbl_fn(ptr, 2, VoidProto)(ctypes.cast(ptr, c_void_p))
        except Exception:
            pass

    def hr_check(hr: int, what: str):
        if hr != 0:
            raise RuntimeError("%s 失败: 0x%08X" % (what, hr & 0xFFFFFFFF))

    # ---- 执行 ----
    # COM 初始化：失败（含 RPC_E_CHANGED_MODE）一律忽略——后续调用不依赖套间类型
    ole32.CoInitializeEx(None, 0x2)

    out = c_void_p()
    hr_check(ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1,
                                    ctypes.byref(IID_IShellLinkW),
                                    ctypes.byref(out)), "CoCreateInstance")
    link = ctypes.cast(out, ctypes.POINTER(IFACE))

    try:
        # IShellLinkW::SetPath 位于虚表索引 20（IUnknown 3 + 前 17 个方法）
        vtbl_fn(link, 20, SetPathProto)(ctypes.cast(link, c_void_p), target_exe)

        pf_out = c_void_p()
        hr_check(vtbl_fn(link, 0, QIProto)(ctypes.cast(link, c_void_p),
                                           ctypes.byref(IID_IPersistFile),
                                           ctypes.byref(pf_out)),
                 "QueryInterface(IPersistFile)")
        pf = ctypes.cast(pf_out, ctypes.POINTER(IFACE))

        ps_out = c_void_p()
        hr_check(vtbl_fn(link, 0, QIProto)(ctypes.cast(link, c_void_p),
                                           ctypes.byref(IID_IPropertyStore),
                                           ctypes.byref(ps_out)),
                 "QueryInterface(IPropertyStore)")
        store = ctypes.cast(ps_out, ctypes.POINTER(IFACE))

        try:
            pv = PROPVARIANT()
            propsys.InitPropVariantFromString(c_wchar_p(aumid), ctypes.byref(pv))
            hr_check(vtbl_fn(store, 6, SetValueProto)(
                ctypes.cast(store, c_void_p),
                ctypes.byref(PKEY_AppUserModel_ID), ctypes.byref(pv)),
                "SetValue(AppUserModel.ID)")
            hr_check(vtbl_fn(store, 7, VoidProto)(
                ctypes.cast(store, c_void_p)), "Commit")
        finally:
            release(store)

        try:
            # IPersistFile::Save 位于虚表索引 6
            hr_check(vtbl_fn(pf, 6, SaveProto)(ctypes.cast(pf, c_void_p),
                                               lnk_path, True), "Save")
        finally:
            release(pf)
    finally:
        release(link)
