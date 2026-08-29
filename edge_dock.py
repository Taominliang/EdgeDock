#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EdgeDock v1.5 —— 桌面边缘侧边栏快捷启动器
==========================================
功能：
  * 平时隐藏为屏幕边缘细条，鼠标悬停（可自定义延迟）后平滑展开为应用网格
  * 鼠标离开后（可自定义延迟）自动收回；应用多时上下平滑滚动（手机侧边栏式）
  * 高清图标：优先提取 48px 大图标并超采样放大，自动缓存
  * 图形化设置面板：右键「设置…」可视化修改全部参数，修改实时生效，无需手改代码/JSON
  * 图标悬停高亮、半透明面板（alpha）、置顶层级、开机自启动（注册表 Run）
  * v1.2：窗口圆角（Win32 SetWindowRgn）、无图标占位块圆角并自动与背景对比配色
  * v1.3：DPI 感知（解决模糊）、DWM 抗锯齿圆角、亚克力毛玻璃背景、运行状态指示点、
    图标大小/边距/圆角独立配置、左键点击空白收起、启动不抢焦点、设置实时生效+撤销/恢复默认
  * v1.4：移除运行状态指示点（改为 Win32 API 枚举进程，消除黑窗口）、修复设置面板闪退
  * v1.5：移除亚克力/背景图入口（加载更轻快）、修复图标贴右溢出、滚轮在图标上可滚动、
    文字大小/显示文字开关、右键打开所在位置

依赖：仅 Python 标准库（tkinter / ctypes / json / zlib / struct / winreg）
运行：python edge_dock.py    （Windows + Python 3.8+，需支持 tkinter）
"""

import json
import os
import sys
import zlib
import struct
import base64
import math
import time
import hashlib
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog, messagebox, colorchooser, simpledialog

try:
    import ctypes
    from ctypes import wintypes
except Exception:
    ctypes = None
    wintypes = None

APP_NAME = "EdgeDock"
REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
TRANSPARENT_KEY = "#010203"    # 亚克力模式下用作颜色键，该色像素全部透明


def _enable_dpi_awareness():
    """开启进程级 DPI 感知，避免 Windows 把 tkinter 窗口整体位图拉伸导致模糊。
    必须在创建 Tk 窗口前调用。返回当前 DPI 缩放系数（= 实际DPI / 96）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0

DEFAULT_CONFIG = {
    "dock_position": "right",          # 侧边栏位置: left / right
    "hidden_width": 6,                 # 平时隐藏条宽度(px)
    "show_delay_ms": 150,              # 鼠标悬停到展开的延迟(ms)
    "hide_delay_ms": 500,              # 鼠标离开到收起的延迟(ms)
    "columns": 4,                      # 每行多少个应用
    "padding": 10,                     # 网格内边距(px)
    "spacing": 8,                      # 图标间距(px)
    "dock_width": 280,                 # 展开后窗口宽度(px)
    "dock_height": 420,                # 展开后窗口高度(px)
    "bg_color": "#1e1e2e",             # 展开时背景颜色
    "bg_image": "",                    # 背景图片路径(支持png/gif)，留空为纯色
    "hidden_color": "#3a3a4a",         # 平时隐藏条颜色
    "text_color": "#ffffff",           # 应用名称文字颜色
    "text_size": 8,                    # 应用名称文字大小(px)
    "show_text": True,                 # 是否显示应用名称文字
    "hover_color": "#4a4a6a",          # 图标悬停高亮颜色
    "corner_radius": 12,               # 展开面板圆角半径(px)，0 为直角；仅 corner_mode=region 时生效
    "corner_mode": "dwm",              # 圆角实现: dwm=Win11抗锯齿圆角(推荐) / region=自定义半径 / none=直角
    "acrylic": False,                  # 亚克力毛玻璃背景(需要 Win11)
    "icon_size": 0,                    # 图标固定尺寸(px)，0=自动按格子大小
    "icon_margin": 10,                 # 图标与格子边缘的内边距(px)，仅 icon_size=0 时生效
    "icon_corner": True,               # 图标本身四角是否做圆角裁剪
    "click_blank_shrink": True,        # 左键点击面板空白处直接收起
    "topmost": True,                   # 是否置顶显示
    "alpha": 1.0,                      # 展开面板透明度 0.5~1.0
    "autostart": False,                # 是否开机自启动
    "start_hidden": False,             # 最小化运行：启动后完全隐藏，鼠标移到屏幕边缘唤出
    "apps": [
        {"name": "文件资源管理器", "path": "explorer.exe"},
        {"name": "记事本", "path": "notepad.exe"},
        {"name": "计算器", "path": "calc.exe"},
        {"name": "画图", "path": "mspaint.exe"},
        {"name": "命令提示符", "path": "cmd.exe"},
        {"name": "PowerShell", "path": "powershell.exe"},
        {"name": "任务管理器", "path": "taskmgr.exe"},
        {"name": "系统设置", "path": "ms-settings:"}
    ]
}

# Win32 常量
SHGFI_ICON = 0x100
SHGFI_LARGEICON = 0x0

# 主题色板（bg/hidden/text/hover 四项，与 config 键一一对应）
THEMES = {
    "dark": {"bg_color": "#1e1e2e", "hidden_color": "#3a3a4a",
             "text_color": "#ffffff", "hover_color": "#4a4a6a"},
    "light": {"bg_color": "#f2f3f5", "hidden_color": "#c8c9cc",
              "text_color": "#222222", "hover_color": "#d5d7de"},
}


def _detect_system_theme():
    """读取 Windows 应用浅/深色主题设置，返回 'light' 或 'dark'。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(k)
        return "light" if v else "dark"
    except Exception:
        return "dark"


def _enum_monitors():
    """枚举所有显示器工作区，返回 [(left, top, right, bottom), ...]，主屏(含0,0)排最前。"""
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        monitors = []
        CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.POINTER(RECT), ctypes.c_double)

        def _cb(_h, _d, r, _dd):
            monitors.append((r.contents.left, r.contents.top,
                             r.contents.right, r.contents.bottom))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, CB(_cb), 0)
        if not monitors:
            return [(0, 0, ctypes.windll.user32.GetSystemMetrics(0),
                     ctypes.windll.user32.GetSystemMetrics(1))]
        monitors.sort(key=lambda r: (0 if r[0] == 0 and r[1] == 0 else 1, r[0], r[1]))
        return monitors
    except Exception:
        return [(0, 0, 1920, 1080)]


# ---------------------------------------------------------------------------
# PNG 编码（标准库手写，用于把提取的图标转为 tkinter 可显示的 PNG）
# ---------------------------------------------------------------------------
def encode_png(w, h, rgba):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8bit RGBA
    rows = b""
    stride = w * 4
    for y in range(h):
        rows += b"\x00" + rgba[y * stride:(y + 1) * stride]
    idat = zlib.compress(rows, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def decode_png(data):
    """解码 8bit RGBA PNG 为 (w, h, rgba bytes)；支持 filter 0-4。失败返回 None。"""
    try:
        if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        pos = 8
        w = h = None
        bit = color = None
        idat = b""
        while pos + 8 <= len(data):
            ln = struct.unpack(">I", data[pos:pos + 4])[0]
            tag = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + ln]
            if tag == b"IHDR":
                w, h, bit, color = struct.unpack(">IIBB", body[:10])
            elif tag == b"IDAT":
                idat += body
            pos += 12 + ln
        if not w or not h or bit != 8 or color != 6:
            return None
        raw = zlib.decompress(idat)
        stride = w * 4
        out = bytearray(w * h * 4)
        prev = bytearray(stride)
        p = 0
        for y in range(h):
            f = raw[p]
            p += 1
            line = bytearray(raw[p:p + stride])
            p += stride
            if f == 1:
                for x in range(4, stride):
                    line[x] = (line[x] + line[x - 4]) & 0xFF
            elif f == 2:
                for x in range(stride):
                    line[x] = (line[x] + prev[x]) & 0xFF
            elif f == 3:
                for x in range(stride):
                    a = line[x - 4] if x >= 4 else 0
                    line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
            elif f == 4:
                for x in range(stride):
                    a = line[x - 4] if x >= 4 else 0
                    b = prev[x]
                    c = prev[x - 4] if x >= 4 else 0
                    pa = abs(b - c)
                    pb = abs(a - c)
                    pc = abs(a + b - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[x] = (line[x] + pr) & 0xFF
            out[y * stride:(y + 1) * stride] = line
            prev = line
        return (w, h, bytes(out))
    except Exception:
        return None


def resize_rgba(src, sw, sh, dw, dh):
    """双线性插值缩放 RGBA 图像（高清放大核心）。"""
    if sw == dw and sh == dh:
        return src
    out = bytearray(dw * dh * 4)
    xs = sw / float(dw)
    ys = sh / float(dh)
    for y in range(dh):
        sy = y * ys
        y0 = int(sy)
        y1 = y0 + 1 if y0 + 1 < sh else y0
        fy = sy - y0
        for x in range(dw):
            sx = x * xs
            x0 = int(sx)
            x1 = x0 + 1 if x0 + 1 < sw else x0
            fx = sx - x0
            o = (y * dw + x) * 4
            i00 = (y0 * sw + x0) * 4
            i01 = (y0 * sw + x1) * 4
            i10 = (y1 * sw + x0) * 4
            i11 = (y1 * sw + x1) * 4
            for c in range(4):
                v = (src[i00 + c] * (1 - fx) * (1 - fy)
                     + src[i01 + c] * fx * (1 - fy)
                     + src[i10 + c] * (1 - fx) * fy
                     + src[i11 + c] * fx * fy)
                out[o + c] = int(v + 0.5)
    return bytes(out)


def fit_rgba_center(rgba, sw, sh, dw, dh):
    """等比缩放并居中到 dw x dh 画布，四周透明。"""
    if sw <= 0 or sh <= 0:
        return None
    scale = min(dw / float(sw), dh / float(sh))
    nw = max(1, int(sw * scale))
    nh = max(1, int(sh * scale))
    tmp = resize_rgba(rgba, sw, sh, nw, nh)
    out = bytearray(dw * dh * 4)
    ox = (dw - nw) // 2
    oy = (dh - nh) // 2
    for y in range(nh):
        s = y * nw * 4
        e = s + nw * 4
        d = (oy + y) * dw * 4 + ox * 4
        out[d:d + nw * 4] = tmp[s:e]
    return bytes(out)


def apply_corner_alpha(rgba, w, h, r):
    """把 RGBA 图像四角做成圆角（圆角外像素 alpha 置 0）。返回新 bytes。"""
    if w <= 0 or h <= 0 or r <= 0:
        return rgba
    r = min(r, w // 2, h // 2)
    try:
        buf = bytearray(rgba)
        r2 = r * r
        for y in range(r):
            dy = r - 1 - y
            for x in range(r):
                dx = r - 1 - x
                if dx * dx + dy * dy > r2:
                    # 左上
                    i = (y * w + x) * 4 + 3
                    buf[i] = 0
                    # 右上
                    i = (y * w + (w - 1 - x)) * 4 + 3
                    buf[i] = 0
                    # 左下
                    i = ((h - 1 - y) * w + x) * 4 + 3
                    buf[i] = 0
                    # 右下
                    i = ((h - 1 - y) * w + (w - 1 - x)) * 4 + 3
                    buf[i] = 0
        return bytes(buf)
    except Exception:
        return rgba


# ---------------------------------------------------------------------------
# Win32 结构
# ---------------------------------------------------------------------------
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class SHFILEINFO(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", ctypes.c_wchar * 260),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


# ---------------------------------------------------------------------------
# 图标提取：HICON -> 高清 PNG
# ---------------------------------------------------------------------------
def _hicon_to_png(hicon, want):
    """从 HICON 提取像素，超采样缩放到 want x want，编码 PNG。失败返回 None。"""
    if ctypes is None:
        return None
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
        user32.GetIconInfo.restype = wintypes.BOOL
        gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
        gdi32.GetObjectW.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                    wintypes.UINT, ctypes.c_void_p,
                                    ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]
        gdi32.GetDIBits.restype = ctypes.c_int

        info = ICONINFO()
        if not user32.GetIconInfo(hicon, ctypes.byref(info)):
            return None
        hbm = info.hbmColor or info.hbmMask
        bm = BITMAP()
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
            return None
        w, h = int(bm.bmWidth), int(bm.bmHeight)
        if w <= 0 or h <= 0:
            return None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h          # 负数: 自顶向下
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(0)
        try:
            if not gdi32.GetDIBits(hdc, hbm, 0, h,
                                   ctypes.cast(buf, ctypes.c_void_p),
                                   ctypes.byref(bmi), 0):
                return None
        finally:
            user32.ReleaseDC(0, hdc)

        raw = buf.raw
        rgba = bytearray(w * h * 4)
        for i in range(w * h):
            j = i * 4
            rgba[j] = raw[j + 2]
            rgba[j + 1] = raw[j + 1]
            rgba[j + 2] = raw[j]
            rgba[j + 3] = raw[j + 3]
        fitted = fit_rgba_center(bytes(rgba), w, h, want, want)
        if fitted is None:
            return None
        return encode_png(want, want, fitted)
    except Exception:
        return None


def extract_icon_png(target_path, want=64):
    """从 exe/lnk/ico 提取高清图标 PNG bytes；失败返回 None。
    优先 SHGetFileInfo 大图标(48px)，失败回退 ExtractIconEx(32px)，再超采样到 want。"""
    if ctypes is None or not target_path:
        return None
    hicon = None
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                           ctypes.POINTER(wintypes.HICON),
                                           ctypes.POINTER(wintypes.HICON),
                                           ctypes.c_uint]
        shell32.ExtractIconExW.restype = ctypes.c_uint
        shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_void_p, ctypes.c_uint,
                                           wintypes.DWORD]
        shell32.SHGetFileInfoW.restype = ctypes.c_ulong
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int

        # 1) .lnk 优先 ExtractIconEx：直接取快捷方式指向程序本身的图标
        is_lnk = target_path.lower().endswith(".lnk")
        if is_lnk:
            try:
                h = wintypes.HICON()
                if shell32.ExtractIconExW(target_path, 0, ctypes.byref(h), None, 1) and h:
                    hicon = h
            except Exception:
                hicon = None

        # 2) SHGetFileInfo 大图标（exe / 目录 / ico 等真实存在的对象）
        if not hicon and os.path.exists(target_path):
            try:
                shfi = SHFILEINFO()
                if shell32.SHGetFileInfoW(target_path, 0, ctypes.byref(shfi),
                                          ctypes.sizeof(SHFILEINFO),
                                          SHGFI_ICON | SHGFI_LARGEICON):
                    hicon = shfi.hIcon
            except Exception:
                hicon = None

        # 3) 回退 ExtractIconEx（支持 PATH 系统命令）
        if not hicon:
            h = wintypes.HICON()
            if shell32.ExtractIconExW(target_path, 0, ctypes.byref(h), None, 1) and h:
                hicon = h

        if not hicon:
            return None
        try:
            return _hicon_to_png(hicon, want)
        finally:
            try:
                user32.DestroyIcon(hicon)
            except Exception:
                pass
    except Exception:
        return None


# 可选择性添加的系统内置应用（存在性自动过滤）
SYS_APPS = [
    ("文件资源管理器", "explorer.exe"),
    ("系统设置", "ms-settings:"),
    ("计算器", "calc.exe"),
    ("记事本", "notepad.exe"),
    ("画图", "mspaint.exe"),
    ("命令提示符", "cmd.exe"),
    ("PowerShell", "powershell.exe"),
    ("任务管理器", "taskmgr.exe"),
    ("控制面板", "control.exe"),
    ("放大镜", "magnify.exe"),
    ("截图工具", "snippingtool.exe"),
    ("写字板", "wordpad.exe"),
    ("设备管理器", "devmgmt.msc"),
    ("磁盘管理", "diskmgmt.msc"),
    ("事件查看器", "eventvwr.msc"),
]


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------
class EdgeDock:
    def __init__(self, cfg_path):
        self.cfg_path = cfg_path
        self.cfg = self._load_config()
        self.icon_dir = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "icons")
        try:
            os.makedirs(self.icon_dir, exist_ok=True)
        except Exception:
            pass

        self._dpi_scale = _enable_dpi_awareness()
        self._photos = []       # 保持 PhotoImage 引用防止被回收
        self._icon_cache = {}        # (path, icon_path, target, corner_r) -> PhotoImage
        self._icon_cache_order = []  # FIFO 顺序，控制缓存上限
        self.bg_photo = None
        self._canvas = None
        self._animating = False
        self.expanded = False
        self.show_job = None
        self.hide_job = None
        self._settings_open = False
        self._settings_dlg = None
        self._keep_visible = False
        self._wheel_acc = 0
        self._live_job = None   # 实时应用节流
        self._save_job = None   # 实时保存 debounce

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.cfg.get("topmost", True)))
        self.root.bind_all("<Enter>", self._on_enter)
        self.root.bind_all("<Leave>", self._on_leave)
        self.root.bind_all("<Button-3>", self._popup_menu)
        self.root.bind_all("<Button-2>", self._popup_menu)
        # 滚轮全局转发：图标子控件不冒泡 MouseWheel，必须 bind_all 统一处理
        self.root.bind_all("<MouseWheel>", self._on_wheel)

        # 多显示器与主题
        self._monitors = _enum_monitors()
        self._apply_theme()
        self._update_monitor()
        # 应用网格视图状态（分组导航 / 搜索 / 拖拽）
        self._group_stack = []
        self._search_var = None
        self._search_entry = None
        self._back_btn = None
        self._drag = {"item": None, "active": False, "sx": 0, "sy": 0}
        self._search_locked = False
        self._last_click = {}    # path -> 上次点击时间（双击去重）
        self._launch_jobs = {}   # path -> 延迟启动 job
        self._group_click = {}   # id(group) -> 上次点击时间（分组双击去重）
        self._group_jobs = {}    # id(group) -> 分组延迟进入 job
        self._inline_ask_bar = None  # 内嵌输入行（重命名/新建分组）
        self._inline_var = None
        self._inline_entry = None
        self._dnd_holder = None  # 外部拖放 WndProc 引用（防 GC）
        self._inner = None
        self._scrollbar = None

        self._sync_autostart()
        self._fully_hidden = False   # 最小化运行：完全隐藏（连隐藏条都不显示）
        self._edge_poll_job = None   # 完全隐藏时的边缘鼠标轮询任务
        self._set_hidden_ui()
        self._place_hidden()
        # 启动不抢焦点（静默运行在最小化状态）
        try:
            self._prev_fg = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            self._prev_fg = 0
        self.root.after(60, self._noactivate)
        # 注册外部文件拖放（WM_DROPFILES）
        self.root.after(200, self._enable_drag_drop)
        # 最小化运行：启动后完全隐藏，鼠标移到屏幕边缘唤出
        if bool(self.cfg.get("start_hidden", False)):
            self._enter_fully_hidden()

    def _noactivate(self):
        """启动后把焦点还给先前的前台窗口，实现静默启动不抢焦点。"""
        try:
            if self._prev_fg:
                ctypes.windll.user32.SetForegroundWindow(self._prev_fg)
        except Exception:
            pass

    # ---------------- 最小化运行（完全隐藏） ----------------
    def _enter_fully_hidden(self):
        """完全隐藏：窗口 withdraw，仅保留边缘轮询，鼠标到屏幕边缘唤出。"""
        if getattr(self, "_fully_hidden", False):
            return
        for job in (self.hide_job, self.show_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self.hide_job = None
        self.show_job = None
        self._fully_hidden = True
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._start_edge_poll()

    def _start_edge_poll(self):
        if self._edge_poll_job is not None:
            try:
                self.root.after_cancel(self._edge_poll_job)
            except Exception:
                pass
        self._edge_poll_job = None
        self._poll_edge()

    def _edge_hit(self):
        """鼠标是否到达 Dock 所在屏幕边缘（完全隐藏时的唤出条件）。"""
        try:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            x, y = int(pt.x), int(pt.y)
        except Exception:
            return False
        if y < self._mon_y or y >= self._mon_y + self._mon_h:
            return False
        if self.cfg.get("dock_position", "right") == "left":
            return x <= self._mon_x + 3
        return x >= self._mon_x + self._mon_w - 3

    def _poll_edge(self):
        try:
            self._edge_poll_job = None
            if not getattr(self, "_fully_hidden", False):
                return
            if not self._edge_hit():
                self._edge_poll_job = self.root.after(120, self._poll_edge)
                return
            self._wake_from_hidden()
        except Exception:
            pass

    def _wake_from_hidden(self):
        """鼠标到达屏幕边缘：恢复隐藏条并展开 Dock。"""
        self._fully_hidden = False
        try:
            self.root.deiconify()
        except Exception:
            pass
        self._set_hidden_ui()
        self._place_hidden()
        if self.expanded or self._animating:
            return
        delay = max(0, int(self.cfg.get("show_delay_ms", 150)))
        self.show_job = self.root.after(delay, self._do_expand)

    # ---------------- 配置 ----------------
    def _load_config(self):
        cfg = dict(DEFAULT_CONFIG)
        if os.path.exists(self.cfg_path):
            try:
                with open(self.cfg_path, "r", encoding="utf-8") as f:
                    user = json.load(f)
                if isinstance(user, dict):
                    for k, v in user.items():
                        cfg[k] = v
            except Exception:
                pass
        else:
            self._save_config(cfg)
        return cfg

    def _save_config(self, cfg=None):
        cfg = cfg or self.cfg
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------------- 开机自启动 ----------------
    def _autostart_cmd(self):
        script = os.path.abspath(sys.argv[0])
        return '"%s" "%s"' % (sys.executable, script)

    def _is_autostart(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, APP_NAME)
                return True
            finally:
                winreg.CloseKey(key)
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _set_autostart(self, enabled):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN, 0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self._autostart_cmd())
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
            self.cfg["autostart"] = enabled
            self._save_config()
            return True
        except Exception:
            return False

    def _sync_autostart(self):
        want = bool(self.cfg.get("autostart", False))
        try:
            if want != self._is_autostart():
                self._set_autostart(want)
        except Exception:
            pass

    # ---------------- 主题与显示器 ----------------
    def _apply_theme(self):
        """颜色主题：auto 跟随系统 / light / dark / custom 使用自定义颜色。"""
        t = str(self.cfg.get("theme", "auto")).lower()
        if t == "custom":
            return
        if t == "auto":
            t = _detect_system_theme()
        colors = THEMES.get(t, THEMES["dark"])
        for k, v in colors.items():
            self.cfg[k] = v

    def _update_monitor(self):
        """根据 config.monitor 刷新当前显示器工作区坐标。"""
        try:
            idx = int(self.cfg.get("monitor", 0))
        except Exception:
            idx = 0
        if not (0 <= idx < len(self._monitors)):
            idx = 0
        self._mon_index = idx
        r = self._monitors[idx]
        self._mon_x, self._mon_y = r[0], r[1]
        self._mon_w, self._mon_h = r[2] - r[0], r[3] - r[1]

    # ---------------- 窗口布局 ----------------
    def _side_x(self, w):
        if self.cfg.get("dock_position", "right") == "left":
            return self._mon_x
        return self._mon_x + self._mon_w - w

    def _s(self, v):
        """按 DPI 缩放系数换算配置像素值，保证高分辨率缩放下视觉尺寸与 100% 一致。"""
        try:
            return int(round(float(v) * self._dpi_scale))
        except Exception:
            return int(v)

    def _is_acrylic(self):
        return bool(self.cfg.get("acrylic", False)) and ctypes is not None

    def _bg_color(self):
        """展开面板实际背景色：亚克力模式下返回透明键色。"""
        return TRANSPARENT_KEY if self._is_acrylic() else self.cfg.get("bg_color", "#1e1e2e")

    def _set_hidden_ui(self):
        self.root.configure(bg=self.cfg.get("hidden_color", "#3a3a4a"))
        # 收起状态恢复正常渲染（清除透明键与亚克力）
        try:
            self.root.attributes("-transparentcolor", "")
        except Exception:
            pass
        self._apply_acrylic(False)

    def _place_hidden(self):
        w = self._s(int(self.cfg.get("hidden_width", 6)))
        self.root.geometry("%dx%d+%d+%d" % (w, self._mon_h, self._side_x(w), self._mon_y))
        # 隐藏条：小圆角（胶囊感），防止直角扎眼
        self._apply_round_rect(w, self._mon_h, min(4, self._corner_radius()))

    def _corner_radius(self):
        try:
            return self._s(max(0, min(40, int(self.cfg.get("corner_radius", 12)))))
        except Exception:
            return self._s(12)

    def _apply_round_rect(self, w, h, radius):
        """窗口圆角：
        corner_mode=dwm   ->  Win11 DWM 原生圆角，带抗锯齿（corner_radius 不生效）
        corner_mode=region->  SetWindowRgn 自定义半径圆角
        corner_mode=none  ->  直角（清除旧圆角）
        DWM 调用失败时自动回退 region。"""
        if not (ctypes and wintypes):
            return
        mode = str(self.cfg.get("corner_mode", "dwm")).lower()
        try:
            hwnd = self.root.winfo_id()
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            # 先清除旧 region，避免 dwm/none 时残留
            try:
                user32.SetWindowRgn(hwnd, None, True)
            except Exception:
                pass
            if mode == "dwm" and radius > 0:
                try:
                    DWMWA_WINDOW_CORNER_PREFERENCE = 33
                    DWMWCP_ROUND = 2
                    v = ctypes.c_int(DWMWCP_ROUND)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                        ctypes.byref(v), ctypes.sizeof(v))
                    return
                except Exception:
                    mode = "region"   # DWM 不可用则回退 region
            if mode == "region" and radius > 0:
                w = max(1, int(w))
                h = max(1, int(h))
                r = min(radius, w // 2, h // 2)
                rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
                if rgn:
                    user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:
            pass

    def _apply_acrylic(self, enable):
        """亚克力毛玻璃背景（Win10 1803+ / Win11）。enable=False 时恢复普通窗口。"""
        if ctypes is None:
            return False
        try:
            class ACCENT_POLICY(ctypes.Structure):
                _fields_ = [("AccentState", ctypes.c_int),
                            ("AccentFlags", ctypes.c_int),
                            ("GradientColor", ctypes.c_uint),
                            ("AnimationId", ctypes.c_int)]

            class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
                _fields_ = [("Attribute", ctypes.c_int),
                            ("Data", ctypes.c_void_p),
                            ("SizeOfData", ctypes.c_size_t)]

            WCA_ACCENT_POLICY = 19
            ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
            ACCENT_DISABLED = 0
            hwnd = self.root.winfo_id()
            accent = ACCENT_POLICY()
            if enable:
                accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
                accent.AccentFlags = 2   # 允许鼠标穿透由颜色键控制，保留绘画
                # 色调：取 bg_color 的 RGB，透明度 0xC8（200）
                try:
                    c = str(self.cfg.get("bg_color", "#1e1e2e"))
                    cr = int(c[1:3], 16)
                    cg = int(c[3:5], 16)
                    cb = int(c[5:7], 16)
                except Exception:
                    cr, cg, cb = 0x1e, 0x1e, 0x2e
                accent.GradientColor = (0xC8000000 | (cb << 16) | (cg << 8) | cr)
            else:
                accent.AccentState = ACCENT_DISABLED
            data = WINDOWCOMPOSITIONATTRIBDATA()
            data.Attribute = WCA_ACCENT_POLICY
            data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
            data.SizeOfData = ctypes.sizeof(accent)
            ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
            return True
        except Exception:
            return False

    # ---------------- 展开 / 收起 ----------------
    def _do_expand(self):
        self.show_job = None
        if self.expanded or self._animating:
            return
        self._animating = True
        # 动画期间只保留纯色背景，避免每帧重绘图标网格导致卡顿
        self._clear_root_children()
        self.root.configure(bg=self._bg_color())
        if self._is_acrylic():
            try:
                self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            except Exception:
                pass
        self._expand_anim(0)

    def _expand_anim(self, step, total=12):
        if not self._animating:
            return
        pos = self.cfg.get("dock_position", "right")
        w0 = self._s(int(self.cfg.get("hidden_width", 6)))
        w1 = self._s(int(self.cfg.get("dock_width", 280)))
        h1 = self._s(int(self.cfg.get("dock_height", 420)))
        y1 = self._mon_y + max(0, (self._mon_h - h1) // 2)
        p = (step + 1) / float(total)
        t = 1 - (1 - p) * (1 - p)   # ease-out：起步快、收尾缓
        cur_w = int(w0 + (w1 - w0) * t)
        cur_h = int(self._mon_h + (h1 - self._mon_h) * t)
        cur_y = int(y1 * t)
        self.root.geometry("%dx%d+%d+%d" % (cur_w, cur_h, self._side_x(cur_w), cur_y))
        self._apply_round_rect(cur_w, cur_h, self._corner_radius())
        if step + 1 < total:
            self.root.after(10, lambda: self._expand_anim(step + 1, total))
        else:
            self._animating = False
            self.expanded = True
            self._show_grid()
            self.root.after(80, self._check_pointer)

    def _do_shrink(self):
        self.hide_job = None
        if self._settings_open or not self.expanded or self._animating:
            return
        self._animating = True
        # 先移除图标网格，只对纯色窗口做收缩动画，更流畅
        self._clear_root_children()
        self.root.configure(bg=self._bg_color())
        try:
            self.root.attributes("-alpha", 1.0)
        except Exception:
            pass
        self._shrink_anim(0)

    def _shrink_anim(self, step, total=12):
        if not self._animating:
            return
        pos = self.cfg.get("dock_position", "right")
        w0 = self._s(int(self.cfg.get("hidden_width", 6)))
        w1 = self._s(int(self.cfg.get("dock_width", 280)))
        h1 = self._s(int(self.cfg.get("dock_height", 420)))
        y0 = self._mon_y
        y1 = self._mon_y + max(0, (self._mon_h - h1) // 2)
        p = 1 - (step + 1) / float(total)
        t = p * p   # ease-in：先快后缓
        cur_w = int(w0 + (w1 - w0) * t)
        cur_h = int(self._mon_h + (h1 - self._mon_h) * t)
        cur_y = int(y0 + (y1 - y0) * t)
        self.root.geometry("%dx%d+%d+%d" % (cur_w, cur_h, self._side_x(cur_w), cur_y))
        self._apply_round_rect(cur_w, cur_h, self._corner_radius())
        if step + 1 < total:
            self.root.after(10, lambda: self._shrink_anim(step + 1, total))
        else:
            self._animating = False
            self.expanded = False
            self._clear_root_children()
            try:
                self.root.attributes("-alpha", 1.0)
            except Exception:
                pass
            if bool(self.cfg.get("start_hidden", False)):
                # 最小化运行：收起后回到完全隐藏，鼠标到边缘再唤出
                self._enter_fully_hidden()
            else:
                self._set_hidden_ui()
                self._place_hidden()

    # ---------------- 鼠标事件 ----------------
    def _on_wheel(self, e):
        """滚轮滚动应用网格（全局转发）。

        canvas 的 yview_scroll 使用 "units" 依赖 yscrollincrement 配置；
        且 MouseWheel 事件在图标子控件上不会冒泡到 canvas/inner，
        因此统一在这里 bind_all 处理，并只响应指针位于 Dock 窗口内的事件
        （避免在设置面板等独立 Toplevel 上滚动时误动 Dock）。
        """
        if self._canvas is None:
            return
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            w = None
        # 指针明确位于 Dock 之外的独立窗口（如设置面板）时不滚动；
        # 查不到坐标（模拟事件等）时按 Dock 内处理
        if w is not None:
            node = w
            while node is not None:
                if node == self.root:
                    break
                # 设置面板等独立 Toplevel 是 root 的子窗口，链路终点同样是 root，
                # 必须在这里提前拦截，否则会被误判为 Dock 内
                if isinstance(node, tk.Toplevel):
                    return
                try:
                    node = node.master
                except Exception:
                    node = None
            if node is None:
                return
        self._wheel_acc += e.delta
        step = self._wheel_acc // 120
        if step:
            try:
                self._canvas.yview_scroll(-step, "units")
            except Exception:
                pass
            self._wheel_acc %= 120

    def _clear_root_children(self):
        """销毁 Dock 根窗口的子控件（排除独立 Toplevel，如设置面板）。"""
        for w in list(self.root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                continue
            try:
                w.destroy()
            except Exception:
                pass

    def _on_enter(self, _e):
        if self.show_job:
            self.root.after_cancel(self.show_job)
            self.show_job = None
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if not self.expanded and not self._animating:
            delay = max(0, int(self.cfg.get("show_delay_ms", 150)))
            self.show_job = self.root.after(delay, self._do_expand)

    def _on_leave(self, _e):
        if self.show_job:
            self.root.after_cancel(self.show_job)
            self.show_job = None
        if self.expanded or self._animating:
            self.hide_job = self.root.after(60, self._check_pointer)

    def _check_pointer(self):
        self.hide_job = None
        if self._settings_open:
            return
        if getattr(self, "_keep_visible", False):
            # 批量添加/删除后的保持展示期：不自动收起，稍后复查
            self.hide_job = self.root.after(500, self._check_pointer)
            return
        try:
            x = self.root.winfo_pointerx() - self.root.winfo_rootx()
            y = self.root.winfo_pointery() - self.root.winfo_rooty()
            inside = 0 <= x < self.root.winfo_width() and 0 <= y < self.root.winfo_height()
        except Exception:
            inside = False
        if inside:
            return
        if self.expanded and not self._animating:
            delay = max(0, int(self.cfg.get("hide_delay_ms", 500)))
            self.hide_job = self.root.after(delay, self._do_shrink)

    # ---------------- 应用网格（手机侧边栏式） ----------------
    def _show_grid(self):
        self._clear_root_children()
        bg_color = self._bg_color()
        self.root.configure(bg=bg_color)
        if self._is_acrylic():
            try:
                self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            except Exception:
                pass
            self._apply_acrylic(True)
        else:
            try:
                self.root.attributes("-transparentcolor", "")
            except Exception:
                pass
            try:
                self.root.attributes("-alpha", min(1.0, max(0.5, float(self.cfg.get("alpha", 1.0)))))
            except Exception:
                pass

        w = self._s(int(self.cfg.get("dock_width", 280)))
        h = self._s(int(self.cfg.get("dock_height", 420)))
        pad = self._s(int(self.cfg.get("padding", 10)))
        gap = self._s(int(self.cfg.get("spacing", 8)))
        cols = max(1, int(self.cfg.get("columns", 4)))

        self.bg_photo = None
        bg_img = self.cfg.get("bg_image", "")
        if bg_img and os.path.exists(bg_img) and not self._is_acrylic():
            try:
                self.bg_photo = self._load_bg_photo(bg_img, w, h)
            except Exception:
                self.bg_photo = None

        # 顶部工具条：返回按钮 + 快捷搜索
        top = tk.Frame(self.root, bg=bg_color)
        top.pack(side="top", fill="x")
        topbar = tk.Frame(top, bg=bg_color)
        topbar.pack(fill="x", padx=pad, pady=(pad, 0))

        self._back_btn = tk.Button(topbar, text="← 返回", font=("Segoe UI", 8),
                                   relief="flat", bd=0, padx=4, pady=0,
                                   bg=bg_color, fg=self.cfg.get("text_color", "#ffffff"),
                                   activebackground=bg_color,
                                   activeforeground=self.cfg.get("hover_color", "#4a4a6a"),
                                   command=self._back_to_parent)
        self._back_btn.pack(side="left")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._render_items())
        self._search_entry = tk.Entry(topbar, textvariable=self._search_var,
                                      font=("Segoe UI", 9), relief="flat", bd=0,
                                      bg="#ffffff", fg="#222222",
                                      insertbackground="#222222")
        self._search_entry.pack(side="right", fill="x", expand=True, padx=(8, 0), ipady=2)
        self._search_entry.bind("<Button-3>", lambda e: "break")

        # 内嵌输入行（重命名/新建分组）：挂在 topbar 下方，初始隐藏。
        # 不做独立 Toplevel 弹窗，避免 overrideredirect 父窗口下 Windows 前台锁导致点不到按钮。
        self._inline_ask_bar = tk.Frame(top, bg=bg_color)
        self._inline_var = None
        self._inline_entry = None

        canvas = tk.Canvas(self.root, width=w, height=h, highlightthickness=0, bd=0, bg=bg_color)
        canvas.pack(side="left", fill="both", expand=True)
        if self.bg_photo:
            canvas.create_image(w // 2, h // 2, image=self.bg_photo, anchor="center")

        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = tk.Frame(canvas, bg=bg_color)
        inner_id = canvas.create_window((pad, pad), window=inner, anchor="nw")

        self._canvas = canvas
        self._scrollbar = scrollbar
        self._inner = inner
        self._inner_id = inner_id

        # yscrollincrement 决定 yview_scroll("units") 的单次滚动量：
        # 不设置时 Tk 视为 0，滚动完全无效；按一行（图标+文字+间距）高度滚动
        text_h = 24 if self.cfg.get("show_text", True) else 4
        line_h = self._s(int(self.cfg.get("spacing", 8))) + self._calc_cell() + text_h
        canvas.configure(yscrollincrement=max(1, line_h))
        # 滚轮事件已由 __init__ 中 bind_all 全局转发（图标子控件不冒泡 MouseWheel）
        canvas.focus_set()
        if self.cfg.get("click_blank_shrink", True):
            # 左键点击面板空白处直接收起（图标/文字上不会触发，事件被子控件消费）
            canvas.bind("<Button-1>", lambda e: self._do_shrink())

        self._render_items()
        # 网格渲染完成后重设圆角（reload 配置时窗口尺寸可能已变化）
        self._apply_round_rect(w, h, self._corner_radius())

    def _calc_cell(self):
        w = self._s(int(self.cfg.get("dock_width", 280)))
        pad = self._s(int(self.cfg.get("padding", 10)))
        gap = self._s(int(self.cfg.get("spacing", 8)))
        cols = max(1, int(self.cfg.get("columns", 4)))
        # 每个格子 grid 时 padx=gap//2 两侧各留 gap//2，总占宽为 cols*cell + cols*gap，
        # 必须整体小于容器宽度，否则最后一列会溢出到 Dock 右边缘
        half = max(0, gap // 2)
        return max(36, (w - pad * 2 - cols * half * 2) // cols)

    def _current_items(self):
        """当前视图的完整条目列表（顶层 apps 或分组 items）。"""
        if self._group_stack:
            return self._group_stack[-1].get("items", []) or []
        return [a for a in (self.cfg.get("apps", []) or []) if isinstance(a, dict)]

    def _render_items(self):
        if getattr(self, "_inner", None) is None:
            return
        for wgt in self._inner.winfo_children():
            wgt.destroy()
        bg_color = self._bg_color()
        gap = self._s(int(self.cfg.get("spacing", 8)))
        cols = max(1, int(self.cfg.get("columns", 4)))
        cell = self._calc_cell()

        kw = ""
        if self._search_var is not None:
            kw = self._search_var.get().strip().lower()
        items = [a for a in self._current_items()
                 if not kw or kw in str(a.get("name", "")).lower()]
        self._photos.clear()
        for i, item in enumerate(items):
            r, c = divmod(i, cols)
            if item.get("type") == "group":
                btn = self._make_group_button(self._inner, item, cell)
            else:
                btn = self._make_app_button(self._inner, item, cell)
            btn.grid(row=r, column=c, padx=gap // 2, pady=gap // 2)

        self._update_group_nav()
        self._inner.update_idletasks()
        w = self._s(int(self.cfg.get("dock_width", 280)))
        h = self._s(int(self.cfg.get("dock_height", 420)))
        pad = self._s(int(self.cfg.get("padding", 10)))
        need_scroll = self._inner.winfo_reqheight() + pad * 2 > h - pad
        if need_scroll:
            self._scrollbar.pack(side="right", fill="y")
            self._canvas.configure(yscrollcommand=self._scrollbar.set)
        else:
            self._scrollbar.pack_forget()
            self._canvas.configure(yscrollcommand="")
        self._canvas.configure(scrollregion=(0, 0, w, self._inner.winfo_reqheight() + pad * 2))
        try:
            self._canvas.itemconfigure(self._inner_id,
                                       width=max(1, w - pad * 2 - (12 if need_scroll else 0)))
        except Exception:
            pass
        # 搜索进行时禁用拖拽排序
        self._search_locked = bool(kw)

    def _update_group_nav(self):
        if self._back_btn is None:
            return
        self._back_btn.config(state="normal" if self._group_stack else "disabled")

    def _back_to_parent(self):
        if self._group_stack:
            self._group_stack.pop()
        if self._search_var is not None:
            self._search_var.set("")
        self._render_items()

    def _schedule_group_enter(self, group):
        """分组单击延迟进入；260ms 内再次点击视为双击，立即进入一次。

        修复：原实现单击立即进组，双击分组时第一击已进组、第二击落在组内
        第一个应用上，误触启动应用。改为延迟判定后，双击两次点击都在分组
        按钮上，只进入一次，不会穿透到组内应用。
        """
        key = id(group)
        now = time.monotonic()
        prev = self._group_click.get(key)
        job = self._group_jobs.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        if prev is not None and (now - prev) < 0.26:
            # 双击：取消延迟任务，立即进入一次
            self._group_click[key] = None
            self._enter_group(group)
            return
        if job is None:
            self._group_jobs[key] = self.root.after(
                260, lambda k=key: self._enter_group(self._group_by_key(k)))
            self._group_click[key] = now
        # 限制记录量，防止长期运行内存膨胀
        if len(self._group_click) > 200:
            self._group_click.clear()

    def _group_by_key(self, key):
        """按 id() 反查分组对象（供延迟 job 使用，避免闭包持有被删分组）。"""
        for g in (self.cfg.get("apps", []) or []):
            if isinstance(g, dict) and id(g) == key:
                return g
        return None

    def _enter_group(self, group):
        if group is None:
            # 延迟进入期间分组已被删除
            return
        # 清理该分组的待执行进入 job，防止重复进入
        key = id(group)
        job = self._group_jobs.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._group_click[key] = None
        self._group_stack.append(group)
        if self._search_var is not None:
            self._search_var.set("")
        self._render_items()

    # ---------------- 无图标占位块（圆角 + 与背景对比配色） ----------------
    def _placeholder_colors(self):
        """根据背景色动态计算占位块颜色与文字颜色，保证任何主题下都与背景区分。"""
        try:
            bg = str(self.cfg.get("bg_color", "#1e1e2e"))
            if bg.lower() == TRANSPARENT_KEY.lower():
                bg = "#1e1e2e"   # 亚克力模式：按暗色基准计算，保证占位块可见
            r = int(bg[1:3], 16)
            g = int(bg[3:5], 16)
            b = int(bg[5:7], 16)
        except Exception:
            r = g = b = 0x1e
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        if lum > 150:
            # 浅背景 -> 占位块加深，文字用浅色
            pr, pg, pb = max(0, r - 60), max(0, g - 60), max(0, b - 60)
            fg = "#ffffff"
        else:
            # 深背景 -> 占位块提亮，文字用深色
            pr, pg, pb = min(255, r + 60), min(255, g + 60), min(255, b + 60)
            fg = "#222222"
        return "#%02x%02x%02x" % (pr, pg, pb), fg

    @staticmethod
    def _round_rect_pts(x1, y1, x2, y2, r):
        """圆角矩形多边形顶点（smooth=True 拟合圆角）。"""
        return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]

    def _make_placeholder(self, parent, name, cell, bg_color):
        """无图标时用 Canvas 绘制圆角占位块；返回 (canvas, 圆角矩形item)。"""
        ph_bg, ph_fg = self._placeholder_colors()
        cv = tk.Canvas(parent, width=cell, height=cell, highlightthickness=0,
                       bd=0, bg=bg_color)
        r = max(6, cell // 4)
        pts = self._round_rect_pts(2, 2, cell - 2, cell - 2, r)
        rect = cv.create_polygon(pts, smooth=True, fill=ph_bg, outline=ph_bg)
        cv.create_text(cell // 2, cell // 2, text=(name[:2] or "?"),
                       fill=ph_fg, font=("Segoe UI", self._text_size() + 3, "bold"))
        return cv, rect

    def _make_app_button(self, parent, app, cell):
        name = app.get("name") or os.path.splitext(os.path.basename(str(app.get("path", ""))))[0]
        path = str(app.get("path", ""))
        icon_path = str(app.get("icon", "")) if app.get("icon") else ""
        bg_color = self._bg_color()
        hover_color = self.cfg.get("hover_color", "#4a4a6a")

        show_text = bool(self.cfg.get("show_text", True))
        text_h = 24 if show_text else 4
        frame = tk.Frame(parent, bg=bg_color, width=cell, height=cell + text_h)
        frame.pack_propagate(False)

        photo = self._load_icon(path, icon_path, cell)
        ph_rect = None
        if photo:
            lab = tk.Label(frame, image=photo, bg=bg_color)
        else:
            lab, ph_rect = self._make_placeholder(frame, name, cell, bg_color)
        lab.pack(pady=(6, 2))

        tlab = None
        if show_text:
            tlab = tk.Label(frame, text=name, bg=bg_color,
                            fg=self.cfg.get("text_color", "#ffffff"),
                            font=("Segoe UI", self._text_size()))
            tlab.pack()

        # 点击 / 拖拽排序：按下后移动超过阈值进入拖拽，否则视为点击启动
        def _on_press(e):
            self._drag.update({"item": app, "active": False,
                               "sx": e.x_root, "sy": e.y_root})

        def _on_motion(e):
            d = self._drag
            if d["item"] is None:
                return
            if not d["active"]:
                if abs(e.x_root - d["sx"]) + abs(e.y_root - d["sy"]) < 10:
                    return
                d["active"] = True
                try:
                    frame.configure(highlightbackground=hover_color, highlightthickness=2)
                except Exception:
                    pass

        def _on_release(e):
            d = self._drag
            item = d["item"]
            if item is None:
                return
            active = d["active"]
            d["item"] = None
            try:
                frame.configure(highlightthickness=0)
            except Exception:
                pass
            if active:
                self._drop_reorder(item, e)
            elif path:
                self._schedule_launch(path)

        def _on_hover(_e):
            for wgt in frame.winfo_children():
                try:
                    wgt.configure(bg=hover_color)
                except Exception:
                    pass
            try:
                if ph_rect is not None:
                    lab.itemconfigure(ph_rect, fill=hover_color, outline=hover_color)
            except Exception:
                pass

        def _on_unhover(_e):
            for wgt in frame.winfo_children():
                try:
                    wgt.configure(bg=bg_color)
                except Exception:
                    pass
            try:
                if ph_rect is not None:
                    lab.itemconfigure(ph_rect, fill=self._placeholder_colors()[0],
                                      outline=self._placeholder_colors()[0])
            except Exception:
                pass

        def _on_right(_e):
            if self._settings_open:
                return
            m = tk.Menu(self.root, tearoff=0)
            m.add_command(label="启动「%s」" % name,
                          command=lambda: self._launch_app(path))
            m.add_command(label="打开所在位置",
                          command=lambda: self._reveal_in_explorer(path))
            m.add_command(label="更换图标…", command=lambda: self._change_icon(app))
            if app.get("icon"):
                m.add_command(label="恢复默认图标",
                              command=lambda: self._restore_icon(app))
            m.add_separator()
            m.add_command(label="删除「%s」" % name,
                          command=lambda: self._remove_app(app))
            try:
                m.tk_popup(_e.x_root, _e.y_root)
            finally:
                m.grab_release()

        for wgt in (frame, lab) + ((tlab,) if tlab is not None else ()):
            wgt.bind("<ButtonPress-1>", _on_press)
            wgt.bind("<B1-Motion>", _on_motion)
            wgt.bind("<ButtonRelease-1>", _on_release)
            wgt.bind("<Button-3>", _on_right)
            wgt.bind("<Enter>", _on_hover)
            wgt.bind("<Leave>", _on_unhover)
        return frame

    def _icon_target(self, cell):
        """图标目标尺寸：icon_size>0 时固定，否则按格子减去 icon_margin 内边距。"""
        size = int(self.cfg.get("icon_size", 0) or 0)
        margin = max(0, self._s(int(self.cfg.get("icon_margin", 10))))
        if size > 0:
            return max(16, min(cell - 2, self._s(size)))
        return max(16, cell - margin)

    def _icon_corner_r(self, target):
        try:
            if not self.cfg.get("icon_corner", True):
                return 0
            return max(4, target // 4)
        except Exception:
            return 0

    def _load_icon(self, path, icon_path, cell):
        target = self._icon_target(cell)
        key = (path or "", icon_path or "", target, self._icon_corner_r(target))
        hit = self._icon_cache.get(key)
        if hit is not None:
            return hit
        p = self._load_icon_uncached(path, icon_path, cell)
        if p is not None:
            if key in self._icon_cache:
                self._icon_cache_order.remove(key)
            self._icon_cache[key] = p
            self._icon_cache_order.append(key)
            while len(self._icon_cache_order) > 300:
                old = self._icon_cache_order.pop(0)
                self._icon_cache.pop(old, None)
        return p

    def _load_icon_uncached(self, path, icon_path, cell):
        target = self._icon_target(cell)   # 图标精确缩到格子内，下方留出文字空间
        want = max(48, target * 2)    # 超采样，保证清晰
        corner_r = self._icon_corner_r(target)
        # 1) 显式指定图标
        if icon_path and os.path.exists(icon_path):
            try:
                with open(icon_path, "rb") as f:
                    data = f.read()
                dec = decode_png(data)
                if dec:
                    w, h, rgba = dec
                    fitted = fit_rgba_center(rgba, w, h, target, target)
                    if corner_r:
                        fitted = apply_corner_alpha(fitted, target, target, corner_r)
                    p = tk.PhotoImage(data=base64.b64encode(
                        encode_png(target, target, fitted)).decode("ascii"))
                    self._photos.append(p)
                    return p
                p = tk.PhotoImage(file=icon_path)
                p = self._fit_icon(p, target)
                self._photos.append(p)
                return p
            except Exception:
                pass
        if not path:
            return None
        # 2) 提取高清图标并缓存
        key = hashlib.md5(path.lower().encode("utf-8", "ignore")).hexdigest()[:16]
        cache = os.path.join(self.icon_dir, "%s_%d.png" % (key, want))
        data = None
        if os.path.exists(cache):
            try:
                with open(cache, "rb") as f:
                    data = f.read()
            except Exception:
                data = None
        if data is None:
            data = extract_icon_png(path, want)
            if data:
                try:
                    with open(cache, "wb") as f:
                        f.write(data)
                except Exception:
                    pass
        if data:
            try:
                dec = decode_png(data)
                if dec:
                    w, h, rgba = dec
                    fitted = fit_rgba_center(rgba, w, h, target, target)
                    if corner_r:
                        fitted = apply_corner_alpha(fitted, target, target, corner_r)
                    p = tk.PhotoImage(data=base64.b64encode(
                        encode_png(target, target, fitted)).decode("ascii"))
                    self._photos.append(p)
                    return p
                p = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
                p = self._fit_icon(p, target)
                self._photos.append(p)
                return p
            except Exception:
                pass
        return None

    def _fit_icon(self, photo, target):
        try:
            pw, ph = photo.width(), photo.height()
            s = max(1, (max(pw, ph) + target - 1) // max(1, target))
            if s > 1:
                photo = photo.subsample(s, s)
        except Exception:
            pass
        return photo

    def _load_bg_photo(self, bg_img, w, h):
        """加载背景图并缩放到面板尺寸，避免大图解码/渲染卡顿。
        PNG 走解码→快速抽稀→精确缩放流程；其它格式直接 PhotoImage。"""
        try:
            if bg_img.lower().endswith(".png"):
                with open(bg_img, "rb") as f:
                    bdata = f.read()
                dec = decode_png(bdata)
                if dec:
                    bw, bh, brgba = dec
                    # 先快速抽稀（隔行取点），再精确缩放，防止 4K 大图全像素双线性卡顿
                    max_side = max(w, h) * 2
                    step = max(1, int(math.ceil(max(bw, bh) / float(max_side))))
                    if step > 1:
                        nw = (bw + step - 1) // step
                        nh = (bh + step - 1) // step
                        small = bytearray(nw * nh * 4)
                        for yy in range(nh):
                            sy = min(bh - 1, yy * step)
                            for xx in range(nw):
                                sx = min(bw - 1, xx * step)
                                s = (sy * bw + sx) * 4
                                d = (yy * nw + xx) * 4
                                small[d:d + 4] = brgba[s:s + 4]
                        brgba, bw, bh = bytes(small), nw, nh
                    fitted = fit_rgba_center(bytes(brgba), bw, bh, w, h)
                    if fitted:
                        png = encode_png(w, h, fitted)
                        return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
            return tk.PhotoImage(file=bg_img)   # 非 PNG 或解码失败：直接加载（GIF 等）
        except Exception:
            return None

    def _text_size(self):
        try:
            return max(6, min(24, int(self.cfg.get("text_size", 8) or 8)))
        except Exception:
            return 8

    def _launch_app(self, path):
        if not path:
            return
        try:
            os.startfile(path)  # noqa
        except Exception:
            try:
                import subprocess
                subprocess.Popen(path, shell=True)
            except Exception as e:
                try:
                    messagebox.showerror(APP_NAME, "启动失败: %s" % e)
                except Exception:
                    pass

    def _schedule_launch(self, path):
        """单击/双击启动：260ms 内第二次点击视为双击，只启动一次。"""
        try:
            now = time.time()
            prev = self._last_click.get(path)
            job = self._launch_jobs.pop(path, None)
            if prev is not None and now - prev < 0.28:
                # 双击：取消第一次延迟，立即启动一次
                if job is not None:
                    try:
                        self.root.after_cancel(job)
                    except Exception:
                        pass
                self._launch_app(path)
                self._last_click[path] = None
                return
            if job is None:
                self._launch_jobs[path] = self.root.after(
                    260, lambda p=path: self._launch_app(p))
                self._last_click[path] = now
            # 限制记录量，防止长期运行内存膨胀
            if len(self._last_click) > 200:
                self._last_click.clear()
        except Exception:
            self._launch_app(path)

    def _reveal_in_explorer(self, path):
        """在资源管理器中定位应用/目录所在位置。"""
        try:
            if not path:
                return
            p = str(path).strip()
            low = p.lower()
            if low.startswith(("http://", "https://", "ms-settings:", "shell:")):
                return
            import subprocess
            if os.path.isdir(p):
                subprocess.Popen(["explorer", p])
            else:
                subprocess.Popen(["explorer", "/select,", os.path.abspath(p)])
        except Exception:
            pass

    # ---------------- 拖拽排序 / 更换图标 / 分组 ----------------
    def _drop_reorder(self, item, e):
        """拖拽松手：按落点行列重排当前视图条目顺序并保存。"""
        if getattr(self, "_search_locked", False):
            return
        try:
            wx = e.x_root - self._inner.winfo_rootx()
            wy = e.y_root - self._inner.winfo_rooty()
        except Exception:
            return
        gap = int(self.cfg.get("spacing", 8))
        cols = max(1, int(self.cfg.get("columns", 4)))
        cell = self._calc_cell()
        cell_w = cell + gap
        cell_h = cell + 24 + gap
        col = max(0, int(wx // cell_w))
        row = max(0, int(wy // cell_h))
        idx = row * cols + col
        # 直接操作真实容器：_current_items 顶层分支返回拷贝列表，改它不会落盘
        if self._group_stack:
            items = self._group_stack[-1].setdefault("items", [])
        else:
            items = self.cfg.setdefault("apps", [])
        if item not in items:
            return
        cur = items.index(item)
        target = max(0, min(idx, len(items) - 1))
        if target > cur:
            target -= 1
        if target == cur:
            return
        items.remove(item)
        items.insert(target, item)
        self._save_config()
        self._render_items()

    def _change_icon(self, app):
        p = filedialog.askopenfilename(
            title="选择图标文件",
            filetypes=[("图标文件", "*.ico *.png *.exe *.lnk"), ("所有文件", "*.*")])
        if not p:
            return
        app["icon"] = p
        self._save_config()
        self._render_items()

    def _restore_icon(self, app):
        app.pop("icon", None)
        self._save_config()
        self._render_items()

    def _make_group_button(self, parent, group, cell):
        name = str(group.get("name", "未命名分组"))
        items = group.get("items", []) or []
        bg_color = self._bg_color()
        hover_color = self.cfg.get("hover_color", "#4a4a6a")

        frame = tk.Frame(parent, bg=bg_color, width=cell, height=cell + 24)
        frame.pack_propagate(False)

        # 用系统文件夹图标（从用户目录提取一次）
        photo = self._load_icon(os.path.expandvars("%USERPROFILE%"), "", cell)
        ph_rect = None
        if photo:
            lab = tk.Label(frame, image=photo, bg=bg_color)
        else:
            lab, ph_rect = self._make_placeholder(frame, "组", cell, bg_color)
        lab.pack(pady=(6, 2))

        tlab = tk.Label(frame, text="%s(%d)" % (name, len(items)), bg=bg_color,
                        fg=self.cfg.get("text_color", "#ffffff"),
                        font=("Segoe UI", 8))
        tlab.pack()

        def _on_click(_e):
            self._schedule_group_enter(group)

        def _on_hover(_e):
            for wgt in frame.winfo_children():
                try:
                    wgt.configure(bg=hover_color)
                except Exception:
                    pass
            try:
                if ph_rect is not None:
                    lab.itemconfigure(ph_rect, fill=hover_color, outline=hover_color)
            except Exception:
                pass

        def _on_unhover(_e):
            for wgt in frame.winfo_children():
                try:
                    wgt.configure(bg=bg_color)
                except Exception:
                    pass
            try:
                if ph_rect is not None:
                    lab.itemconfigure(ph_rect, fill=self._placeholder_colors()[0],
                                      outline=self._placeholder_colors()[0])
            except Exception:
                pass

        def _on_right(_e):
            if self._settings_open:
                return
            m = tk.Menu(self.root, tearoff=0)
            m.add_command(label="打开分组「%s」" % name,
                          command=lambda: self._enter_group(group))
            m.add_separator()
            m.add_command(label="重命名分组…",
                          command=lambda: self._rename_group(group))
            m.add_command(label="删除分组「%s」" % name,
                          command=lambda: self._remove_group(group))
            try:
                m.tk_popup(_e.x_root, _e.y_root)
            finally:
                m.grab_release()

        for wgt in (frame, lab, tlab):
            wgt.bind("<Button-1>", _on_click)
            wgt.bind("<Button-3>", _on_right)
            wgt.bind("<Enter>", _on_hover)
            wgt.bind("<Leave>", _on_unhover)
        return frame

    def _ask_text(self, title, prompt, initialvalue=""):
        """自定义置顶输入框：替代 simpledialog，避免弹窗被 Dock 收起逻辑销毁，且保证显示在最上层。"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg="#f0f0f0")
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            win.transient(self.root)
        except Exception:
            pass
        pad = 14
        tk.Label(win, text=prompt, bg="#f0f0f0", fg="#222222",
                 font=("Segoe UI", 10)).pack(padx=pad, pady=(pad, 4), anchor="w")
        var = tk.StringVar(value=initialvalue)
        entry = tk.Entry(win, textvariable=var, font=("Segoe UI", 10),
                         width=32, relief="solid", bd=1)
        entry.pack(padx=pad, pady=(0, pad))
        btns = tk.Frame(win, bg="#f0f0f0")
        btns.pack(fill="x", padx=pad, pady=(0, pad))
        result = {}

        def _ok(_e=None):
            result["v"] = var.get()
            try:
                win.destroy()
            except Exception:
                pass

        def _cancel(_e=None):
            try:
                win.destroy()
            except Exception:
                pass

        tk.Button(btns, text="确定", width=8, command=_ok).pack(side="right")
        tk.Button(btns, text="取消", width=8, command=_cancel).pack(side="right", padx=(0, 6))
        win.bind("<Return>", _ok)
        win.bind("<Escape>", _cancel)
        try:
            win.update_idletasks()
            w = win.winfo_reqwidth()
            h = win.winfo_reqheight()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = max(0, self.root.winfo_rooty() - h - 8)
            win.geometry("+%d+%d" % (max(0, x), y))
        except Exception:
            pass
        try:
            win.update_idletasks()
            win.deiconify()
            win.lift()
            # 等待窗口真正显示后再抢焦点/抓取输入，否则 Windows 下 grab 静默失效，
            # 表现为弹窗可见但点不到（需先点其它窗口再点回来才激活）
            win.wait_visibility()
        except Exception:
            pass

        def _force_focus():
            # Windows 前台锁：无用户输入交互时 focus_force 会被系统忽略，
            # 先模拟一次 Alt 键（绕过前台激活限制）再抢焦点
            try:
                import ctypes
                KEYEVENTF_KEYUP = 0x0002
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass
            try:
                win.lift()
                win.focus_force()
                entry.focus_set()
                entry.select_range(0, "end")
            except Exception:
                pass

        _force_focus()
        # 双保险：等窗口完全映射后再补抢一次，覆盖系统延迟激活的场景
        try:
            win.after(60, _force_focus)
            win.after(160, _force_focus)
        except Exception:
            pass
        try:
            win.grab_set()
        except Exception:
            pass
        try:
            self.root.wait_window(win)
        except Exception:
            pass
        return result.get("v")

    # ---------------- 内嵌输入行（替代独立弹窗，规避 Windows 前台锁） ----------------
    def _inline_ask(self, prompt, initialvalue="", on_ok=None):
        """在 Dock 顶部工具条下方内嵌一行输入框（Label+Entry+确定/取消）。

        与独立 Toplevel 弹窗不同：输入行属于 Dock 主窗口自身，不存在
        overrideredirect 父窗口下 Windows 前台锁导致"点不到确定"的问题；
        Entry 始终可获得焦点，回车/按钮均可直接提交。
        """
        # 冻结自动收起
        for job in (self.hide_job, self.show_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self.hide_job = None
        self.show_job = None
        if not self.expanded:
            self._force_expand_show()
        self._settings_open = True

        bar = self._inline_ask_bar
        if bar is None:
            self._settings_open = False
            return
        try:
            if not bar.winfo_exists():
                self._show_grid()
                bar = self._inline_ask_bar
                if bar is None:
                    self._settings_open = False
                    return
        except Exception:
            self._settings_open = False
            return
        # 清空旧内容
        for w in list(bar.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        bg_color = self._bg_color()
        fg_color = self.cfg.get("text_color", "#ffffff")
        pad = self._s(int(self.cfg.get("padding", 10)))
        self._inline_var = tk.StringVar(value=initialvalue)

        def _ok(_e=None):
            val = self._inline_var.get() if self._inline_var is not None else ""
            self._close_inline_ask()
            if on_ok:
                on_ok(val)

        def _cancel(_e=None):
            self._close_inline_ask()
            self.root.after(80, self._check_pointer)

        # 先 pack 右侧按钮，再 pack 左侧 Label+Entry（Entry expand 会吸收剩余空间；
        # 若按钮后 pack，会被压缩到 1px 且排到窗口外，导致"看不到确定"）
        tk.Button(bar, text="确定", font=("Segoe UI", 9), relief="flat",
                  padx=8, command=_ok).pack(side="right", padx=(6, 0))
        tk.Button(bar, text="取消", font=("Segoe UI", 9), relief="flat",
                  padx=8, command=_cancel).pack(side="right", padx=(6, 0))
        tk.Label(bar, text=prompt, bg=bg_color, fg=fg_color,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        entry = tk.Entry(bar, textvariable=self._inline_var,
                         font=("Segoe UI", 9), relief="solid", bd=1,
                         bg="#ffffff", fg="#222222",
                         insertbackground="#222222")
        entry.pack(side="left", fill="x", expand=True, ipady=1)
        self._inline_entry = entry

        try:
            if not bar.winfo_exists():
                self._settings_open = False
                return
        except Exception:
            self._settings_open = False
            return
        bar.pack(fill="x", padx=pad, pady=(4, 0))
        entry.bind("<Return>", _ok)
        entry.bind("<Escape>", _cancel)
        # 确保 Dock 窗口处于前台：内嵌 Entry 才能接收键盘输入。
        # 复用 Alt 键 hack 绕过 Windows 前台锁（右键菜单关闭后焦点可能不在 Dock）。
        try:
            import ctypes
            KEYEVENTF_KEYUP = 0x0002
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        entry.focus_set()
        entry.select_range(0, "end")
        entry.icursor("end")
        # 输入行可见后再补一次聚焦，覆盖系统延迟激活的场景
        try:
            self.root.after(60, lambda: self._inline_entry.focus_set())
        except Exception:
            pass

    def _close_inline_ask(self):
        self._settings_open = False
        bar = self._inline_ask_bar
        if bar is None:
            return
        try:
            bar.pack_forget()
        except Exception:
            pass
        for w in list(bar.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self._inline_var = None
        self._inline_entry = None

    def _new_group(self):
        self._inline_ask("请输入分组名称：",
                         on_ok=self._do_new_group)

    def _do_new_group(self, name):
        name = (name or "").strip()
        if not name:
            self.root.after(80, self._check_pointer)
            return
        apps = self.cfg.setdefault("apps", [])
        apps.append({"type": "group", "name": name, "items": []})
        self._save_config()
        self._reload_config()
        self.root.after(80, self._check_pointer)

    def _rename_group(self, group):
        # 内嵌输入期间冻结自动收起，避免改名后 Dock 收起导致看不到刷新
        self._inline_ask("请输入新的分组名称：",
                         initialvalue=str(group.get("name", "")),
                         on_ok=lambda name: self._do_rename_group(group, name))

    def _do_rename_group(self, group, name):
        name = (name or "").strip()
        if not name:
            self.root.after(80, self._check_pointer)
            return
        group["name"] = name
        self._save_config()
        if self.expanded:
            self._render_items()
        else:
            self._force_expand_show()
        # 输入行关闭后恢复正常指针检测
        self.root.after(80, self._check_pointer)

    def _remove_group(self, group):
        try:
            if not messagebox.askyesno(APP_NAME,
                                       "确定删除分组「%s」及其全部图标吗？" % group.get("name", "")):
                return
        except Exception:
            return
        apps = self.cfg.get("apps", []) or []
        if group in apps:
            apps.remove(group)
        self._save_config()
        self._reload_config()

    def _remove_current_group(self):
        if not self._group_stack:
            return
        group = self._group_stack[-1]
        name = group.get("name", "未命名分组")
        try:
            if not messagebox.askyesno(APP_NAME,
                                       "确定删除分组「%s」及其全部图标吗？" % name):
                return
        except Exception:
            return
        apps = self.cfg.get("apps", []) or []
        if group in apps:
            apps.remove(group)
        self._group_stack = []
        self._save_config()
        self._reload_config()

    def _all_containers(self):
        """返回所有可能存放条目的容器列表（顶层 apps + 各分组 items）。"""
        containers = [self.cfg.get("apps", []) or []]
        for g in containers[0]:
            if isinstance(g, dict) and g.get("type") == "group":
                containers.append(g.get("items", []) or [])
        return containers

    # ---------------- 右键菜单 ----------------
    def _popup_menu(self, e):
        if self._settings_open:
            # 输入框/设置面板打开期间不弹右键菜单，避免点击穿透误触发
            return
        w = self.root.winfo_containing(e.x_root, e.y_root)
        inside = False
        node = w
        while node is not None:
            if node == self.root:
                inside = True
                break
            try:
                node = node.master
            except Exception:
                break
        if not inside:
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="EdgeDock设置…", command=self._open_settings)
        menu.add_command(label="添加应用…", command=self._add_app)
        menu.add_command(label="添加文件夹…", command=self._add_folder)
        menu.add_command(label="从桌面导入…", command=self._add_desktop)
        menu.add_command(label="添加系统应用…", command=self._add_system_apps)
        menu.add_command(label="批量删除…", command=self._batch_remove)
        menu.add_command(label="新建分组…", command=self._new_group)
        if self._group_stack:
            menu.add_command(label="返回上级分组", command=self._back_to_parent)
            menu.add_command(label="重命名当前分组…",
                             command=lambda: self._rename_group(self._group_stack[-1]))
            menu.add_command(label="删除当前分组（含全部图标）",
                             command=self._remove_current_group)
        menu.add_command(label="重新加载配置", command=self._reload_config)
        menu.add_separator()
        st = "已开启" if self._is_autostart() else "已关闭"
        menu.add_command(label="开机自启动（当前%s，点击切换）" % st,
                         command=self._toggle_autostart)
        menu.add_command(label="编辑配置文件", command=self._open_config)
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _open_settings(self):
        if self._settings_open:
            return
        self._settings_open = True
        # 打开设置时冻结自动收起：取消已排队的展开/收起任务，防止面板被误收起
        for job in (self.hide_job, self.show_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self.hide_job = None
        self.show_job = None
        dlg = SettingsDialog(self.root, self.cfg, self._apply_settings,
                             on_live=self._apply_settings_live)
        self._settings_dlg = dlg
        win = dlg.win

        def _closed(_e=None):
            if self._settings_open:
                self._settings_open = False
                self._settings_dlg = None
                if bool(self.cfg.get("start_hidden", False)):
                    # 最小化运行开启：关闭设置后收起并回到完全隐藏
                    if self.expanded and not self._animating:
                        self._do_shrink()
                    elif not self._animating:
                        self._enter_fully_hidden()
                else:
                    # 面板关闭后，若指针不在 Dock 内则恢复正常收起
                    self.root.after(80, self._check_pointer)

        win.protocol("WM_DELETE_WINDOW",
                     lambda: (win.destroy(), _closed()))
        win.bind("<Destroy>", lambda e: _closed() if e.widget is win else None)

    def _apply_settings(self, new_cfg):
        self.cfg = new_cfg
        self._save_config(new_cfg)
        self._apply_theme()
        self._update_monitor()
        self._reload_config()

    def _apply_settings_live(self, new_cfg):
        """设置面板实时预览：只应用不落盘，改动立即生效（撤销后同样走这里）。"""
        self.cfg = new_cfg
        self._apply_theme()
        self._update_monitor()
        self.root.attributes("-topmost", bool(self.cfg.get("topmost", True)))
        if self.expanded:
            self._show_grid()
        else:
            self._set_hidden_ui()
            self._place_hidden()

    def _add_app(self):
        path = filedialog.askopenfilename(
            title="选择要添加的应用",
            filetypes=[("程序", "*.exe *.lnk"), ("所有文件", "*.*")])
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        item = {"name": name, "path": path}
        if self._group_stack:
            # 当前处于分组内：添加到当前分组
            self._group_stack[-1].setdefault("items", []).append(item)
            self._save_config()
            self._render_items()
        else:
            apps = self.cfg.setdefault("apps", [])
            apps.append(item)
            self._save_config()
            self._reload_config()

    def _add_folder(self):
        path = filedialog.askdirectory(title="选择要添加的文件夹")
        if not path:
            return
        name = os.path.basename(os.path.normpath(path)) or path
        item = {"name": name, "path": path, "type": "dir"}
        if self._group_stack:
            self._group_stack[-1].setdefault("items", []).append(item)
            self._save_config()
            self._render_items()
        else:
            apps = self.cfg.setdefault("apps", [])
            apps.append(item)
            self._save_config()
            self._reload_config()

    # ---------------- 批量添加：桌面快捷方式 / 系统应用 ----------------
    def _desktop_dirs(self):
        """收集桌面目录（用户桌面、OneDrive 重定向桌面、公共桌面），去重。"""
        dirs, seen = [], set()
        cand = [os.path.join(os.path.expanduser("~"), "Desktop"),
                os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
                os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")]
        for d in cand:
            try:
                key = os.path.normcase(os.path.abspath(d))
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            if os.path.isdir(d):
                dirs.append(d)
        return dirs

    def _add_desktop(self):
        """扫描桌面 .lnk/.exe，勾选批量加入侧边栏，图标沿用快捷方式指向的程序图标。"""
        entries = []
        seen = set()
        for d in self._desktop_dirs():
            try:
                names = sorted(os.listdir(d))
            except Exception:
                continue
            for fn in names:
                low = fn.lower()
                if not (low.endswith(".lnk") or low.endswith(".exe")):
                    continue
                full = os.path.join(d, fn)
                key = os.path.normcase(full)
                if key in seen:
                    continue
                seen.add(key)
                entries.append((os.path.splitext(fn)[0], full))
        if not entries:
            try:
                messagebox.showinfo(APP_NAME, "桌面上没有找到快捷方式或程序。")
            except Exception:
                pass
            return
        chosen = self._pick_items("从桌面导入", entries,
                                  hint="勾选要加入侧边栏的桌面快捷方式，图标保持与桌面一致。")
        if not chosen:
            return
        self._append_items(chosen)

    def _add_system_apps(self):
        """列出系统内置应用供选择性添加（仅显示本机存在的项）。"""
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        sysdir = os.path.join(windir, "System32")
        entries = []
        for name, exe in SYS_APPS:
            if exe == "ms-settings:":
                entries.append((name, exe))
                continue
            cands = [os.path.join(sysdir, exe)]
            if exe.lower() == "explorer.exe":
                cands.insert(0, os.path.join(windir, exe))
            full = next((c for c in cands if os.path.exists(c)), None)
            if full:
                entries.append((name, full))
        if not entries:
            return
        chosen = self._pick_items("添加系统应用", entries,
                                  hint="勾选要加入侧边栏的系统应用。")
        if not chosen:
            return
        self._append_items(chosen)

    def _append_items(self, items):
        """批量追加条目到当前容器（分组内则进分组），跳过已存在的同名同路径项。"""
        if self._group_stack:
            container = self._group_stack[-1].setdefault("items", [])
        else:
            container = self.cfg.setdefault("apps", [])
        existing = set()
        for it in container:
            if isinstance(it, dict) and it.get("path"):
                existing.add(("%s|%s" % (it.get("name", ""), str(it.get("path", "")))).lower())
        added = 0
        for name, path in items:
            key = ("%s|%s" % (name, str(path))).lower()
            if key in existing:
                continue
            container.append({"name": name, "path": path})
            existing.add(key)
            added += 1
        if added:
            self._save_config()
            self._force_expand_show()

    def _batch_remove(self):
        """勾选批量删除当前视图中的应用（仅移除 Dock 条目，不删磁盘文件）。"""
        if self._group_stack:
            container = self._group_stack[-1].setdefault("items", [])
        else:
            container = self.cfg.get("apps", []) or []
        items = [it for it in container
                 if isinstance(it, dict) and it.get("path")]
        if not items:
            try:
                messagebox.showinfo(APP_NAME, "当前没有可删除的应用。")
            except Exception:
                pass
            return
        entries = [(it.get("name", ""), str(it.get("path", ""))) for it in items]
        chosen = self._pick_items("批量删除", entries,
                                  hint="勾选要从侧边栏删除的应用（仅移除图标，不会删除磁盘上的文件）。",
                                  ok_text="删除",
                                  confirm_msg="确定删除选中的 %d 个应用吗？")
        if not chosen:
            return
        rem = set(("%s|%s" % (n, p)).lower() for n, p in chosen)
        new_list = [it for it in container if not (
            isinstance(it, dict) and it.get("path")
            and ("%s|%s" % (it.get("name", ""), str(it.get("path", "")))).lower() in rem)]
        container[:] = new_list
        self._save_config()
        self._force_expand_show()

    def _pick_items(self, title, entries, hint="", ok_text="添加", confirm_msg=None):
        """通用勾选对话框；返回选中项 (name, path) 列表，取消返回 None。"""
        win = tk.Toplevel(self.root)
        win.title("%s - %s" % (title, APP_NAME))
        win.configure(bg="#f0f0f0")
        win.geometry("460x540")
        win.minsize(380, 360)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        # 打开期间冻结 Dock 自动收起，关闭后恢复
        prev_settings_open = self._settings_open
        self._settings_open = True
        top = tk.Frame(win, bg="#f0f0f0")
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text=hint or title, bg="#f0f0f0", fg="#333333",
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=420).pack(fill="x")
        body = tk.Frame(win, bg="#f0f0f0")
        body.pack(fill="both", expand=True, padx=12, pady=6)
        canvas = tk.Canvas(body, bg="#f0f0f0", highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#f0f0f0")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _wheel(e):
            try:
                canvas.yview_scroll(int(-e.delta / 120), "units")
            except Exception:
                pass

        def _bind_wheel(w):
            try:
                w.bind("<MouseWheel>", _wheel, add="+")
            except Exception:
                pass
            for c in w.winfo_children():
                _bind_wheel(c)

        vars_ = []
        for i, (name, path) in enumerate(entries):
            v = tk.BooleanVar(value=False)
            vars_.append((v, name, path))
            cb = tk.Checkbutton(inner, text=name, variable=v, bg="#f0f0f0",
                                anchor="w", font=("Segoe UI", 9),
                                activebackground="#f0f0f0")
            cb.pack(fill="x", padx=6, pady=1)
        _bind_wheel(inner)

        btns = tk.Frame(win, bg="#f0f0f0")
        btns.pack(fill="x", padx=12, pady=(0, 10))
        result = {}

        def _ok():
            sel = [(n, p) for v, n, p in vars_ if v.get()]
            if confirm_msg and sel:
                try:
                    msg = confirm_msg % len(sel) if "%d" in confirm_msg else confirm_msg
                    if not messagebox.askyesno(APP_NAME, msg):
                        return
                except Exception:
                    return
            result["sel"] = sel
            win.destroy()

        def _all():
            for v, n, p in vars_:
                v.set(True)

        def _none():
            for v, n, p in vars_:
                v.set(False)

        tk.Button(btns, text="全选", width=6, command=_all).pack(side="left")
        tk.Button(btns, text="清空", width=6, command=_none).pack(side="left", padx=(6, 0))
        tk.Button(btns, text="取消", width=8, command=win.destroy).pack(side="right")
        tk.Button(btns, text=ok_text, width=8, command=_ok).pack(side="right", padx=(0, 6))
        try:
            win.transient(self.root)
            win.grab_set()
        except Exception:
            pass
        win.focus_set()
        self.root.wait_window(win)
        self._settings_open = prev_settings_open
        if not prev_settings_open:
            self.root.after(80, self._check_pointer)
        return result.get("sel")

    def _remove_app(self, app):
        try:
            if not messagebox.askyesno(APP_NAME,
                                       "确定从侧边栏删除「%s」吗？" % app.get("name", "")):
                return
        except Exception:
            return
        removed = False
        for container in self._all_containers():
            if app in container:
                container.remove(app)
                removed = True
                break
        if removed:
            self._save_config()
            self._reload_config()

    def _toggle_autostart(self):
        ok = self._set_autostart(not self._is_autostart())
        if ok:
            state = "已开启开机自启动" if self._is_autostart() else "已关闭开机自启动"
            try:
                messagebox.showinfo(APP_NAME, state)
            except Exception:
                pass

    def _open_config(self):
        try:
            os.startfile(self.cfg_path)  # noqa
        except Exception:
            try:
                import subprocess
                subprocess.Popen(["notepad.exe", self.cfg_path])
            except Exception:
                pass

    def _reload_config(self):
        self.cfg = self._load_config()
        self._apply_theme()
        self._update_monitor()
        self.root.attributes("-topmost", bool(self.cfg.get("topmost", True)))
        self._sync_autostart()
        if self.expanded:
            self._show_grid()
        else:
            self._set_hidden_ui()
            self._place_hidden()

    def _force_expand_show(self):
        """批量添加/删除后强制展开 Dock 并渲染结果。

        收起状态下执行 _reload_config 不会重建网格，用户看不到任何变化，
        会误以为功能无效；这里取消排队的收起/展开任务并立即以展开态显示。
        """
        for job in (self.hide_job, self.show_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self.hide_job = None
        self.show_job = None
        self._animating = False
        if not self.expanded:
            pos = self.cfg.get("dock_position", "right")
            w = self._s(int(self.cfg.get("dock_width", 280)))
            h = self._s(int(self.cfg.get("dock_height", 420)))
            y1 = self._mon_y + max(0, (self._mon_h - h) // 2)
            self.root.geometry("%dx%d+%d+%d" % (w, h, self._side_x(w), y1))
            self.expanded = True
        # 保持展开 3 秒供用户查看结果，期间不自动收起
        self._keep_visible = True
        self._show_grid()

        def _release():
            self._keep_visible = False
            self._check_pointer()

        self.root.after(3000, _release)

    # ---------------- 外部文件拖放添加（WM_DROPFILES） ----------------
    def _enable_drag_drop(self):
        """把 Dock 顶层窗口注册为 Win32 拖放目标。

        通过替换 WndProc 捕获 WM_DROPFILES，支持从资源管理器
        直接拖入 .exe/.lnk/.url 等快捷方式到 Dock 添加应用，零依赖。
        """
        try:
            from ctypes import wintypes
            child = self.root.winfo_id()
            hwnd = ctypes.windll.user32.GetAncestor(child, 2)  # GA_ROOT=2
            if not hwnd:
                hwnd = child
            ctypes.windll.shell32.DragAcceptFiles(hwnd, True)

            GWLP_WNDPROC = -4
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                ctypes.c_ssize_t, ctypes.c_ssize_t)
            old_proc = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)

            def _wnd_proc(h, msg, wp, lp):
                if msg == 0x0233:  # WM_DROPFILES
                    try:
                        hdrop = wp
                        n = ctypes.windll.shell32.DragQueryFileW(
                            hdrop, 0xFFFFFFFF, None, 0)
                        paths = []
                        for i in range(n):
                            buf = ctypes.create_unicode_buffer(1024)
                            got = ctypes.windll.shell32.DragQueryFileW(
                                hdrop, i, buf, 1024)
                            if got:
                                paths.append(buf.value[:got])
                        ctypes.windll.shell32.DragFinish(hdrop)
                        if paths:
                            self.root.after_idle(
                                lambda ps=paths: self._add_dropped_files(ps))
                    except Exception:
                        pass
                    return 0
                return ctypes.windll.user32.CallWindowProcW(
                    old_proc, h, msg, wp, lp)

            new_proc = WNDPROC(_wnd_proc)
            # 持有引用防止回调被 GC；拖放接收方窗口句柄
            self._dnd_holder = (new_proc, old_proc, hwnd)
            ctypes.windll.user32.SetWindowLongPtrW(
                hwnd, GWLP_WNDPROC,
                ctypes.cast(new_proc, ctypes.c_void_p).value)
        except Exception:
            pass

    def _add_dropped_files(self, paths):
        """把拖入的文件/文件夹添加到当前视图（顶层或当前分组），自动去重。

        注意：_current_items 顶层分支返回过滤后的新列表，不能直接 append，
        这里始终往真实容器（apps 或分组 items）里写。
        """
        if self._group_stack:
            container = self._group_stack[-1].setdefault("items", [])
        else:
            container = self.cfg.setdefault("apps", [])
        added = 0
        for p in paths:
            p = str(p).strip().strip('"')
            if not p or not os.path.exists(p):
                continue
            low = p.lower()
            if os.path.isdir(p):
                item = {"type": "app", "name": os.path.basename(p.rstrip("\\/")),
                        "path": p, "icon": p}
            elif low.endswith((".exe", ".lnk", ".url", ".bat", ".cmd",
                               ".msi", ".appref-ms", ".jar", ".pyw")):
                item = {"type": "app",
                        "name": os.path.splitext(os.path.basename(p))[0],
                        "path": p}
            else:
                continue
            if any(isinstance(a, dict) and
                   str(a.get("path", "")).lower() == p.lower()
                   for a in container):
                continue
            container.append(item)
            added += 1
        if added:
            self._save_config()
            if self.expanded:
                self._render_items()
            else:
                self._force_expand_show()

    def _quit(self):
        try:
            self.root.destroy()
        except Exception:
            os._exit(0)


# ---------------------------------------------------------------------------
# 图形化设置面板
# ---------------------------------------------------------------------------
class SettingsDialog:
    def __init__(self, master, cfg, on_save, on_live=None):
        self.cfg = dict(cfg)
        self._orig_cfg = dict(cfg)
        self.on_save = on_save
        self.on_live = on_live
        self._live_job = None
        self._building = True
        self.win = tk.Toplevel(master)
        self.win.title("%s 设置" % APP_NAME)
        self.win.resizable(False, False)
        self.win.configure(bg="#f0f0f0")
        self.win.attributes("-topmost", True)
        self._vars = {}
        self._build()
        self._building = False
        # 居中
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        ww, wh = self.win.winfo_width(), self.win.winfo_height()
        self.win.geometry("+%d+%d" % (max(0, (sw - ww) // 2), max(0, (sh - wh) // 2)))
        self.win.grab_set()

    def _row(self, parent, r, label, widget, col2=False):
        tk.Label(parent, text=label, bg="#f0f0f0", anchor="w",
                 width=14 if not col2 else 10).grid(row=r, column=0, sticky="w", padx=(10, 4), pady=4)
        widget.grid(row=r, column=1, sticky="we" if not col2 else "w", padx=(0, 10), pady=4)
        if col2:
            widget.grid_configure(columnspan=1)
        return widget

    def _on_change(self, *_a):
        """任一控件变化后触发实时预览（防抖 350ms）。"""
        if not self.on_live:
            return
        if getattr(self, "_building", False):
            # 构建期间 Spinbox/Scale 等控件创建会回写 var 触发 trace，忽略
            return
        if self._live_job is not None:
            try:
                self.win.after_cancel(self._live_job)
            except Exception:
                pass
        self._live_job = self.win.after(350, self._live_apply)

    def _live_apply(self):
        self._live_job = None
        try:
            self.on_live(self._collect())
        except Exception:
            pass

    def _spin(self, parent, r, label, key, fr, to, step=1, col2=False):
        var = tk.StringVar(value=str(self.cfg.get(key, "")))
        self._vars[key] = var
        var.trace_add("write", self._on_change)
        sb = tk.Spinbox(parent, from_=fr, to=to, increment=step, textvariable=var,
                        width=8, font=("Segoe UI", 9))
        return self._row(parent, r, label, sb, col2)

    def _color(self, parent, r, label, key, col2=False):
        var = tk.StringVar(value=str(self.cfg.get(key, "")))
        self._vars[key] = var
        var.trace_add("write", self._on_change)
        frame = tk.Frame(parent, bg="#f0f0f0")
        en = tk.Entry(frame, textvariable=var, width=9, font=("Segoe UI", 9))
        en.pack(side="left")
        bt = tk.Button(frame, text="选择…", width=6,
                       command=lambda: self._pick_color(var))
        bt.pack(side="left", padx=(4, 0))
        return self._row(parent, r, label, frame, col2)

    def _pick_color(self, var):
        try:
            _, hexv = colorchooser.askcolor(color=var.get() or "#000000", parent=self.win)
            if hexv:
                var.set(hexv)
        except Exception:
            pass

    def _build(self):
        cfg = self.cfg
        top = tk.Frame(self.win, bg="#f0f0f0")
        top.pack(fill="x", padx=0, pady=(10, 0))

        r = 0
        # 位置
        pos = tk.StringVar(value=cfg.get("dock_position", "right"))
        self._vars["dock_position"] = pos
        pos.trace_add("write", self._on_change)
        posf = tk.Frame(top, bg="#f0f0f0")
        tk.Radiobutton(posf, text="右侧", variable=pos, value="right", bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left")
        tk.Radiobutton(posf, text="左侧", variable=pos, value="left", bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left")
        self._row(top, r, "侧边栏位置", posf); r += 1

        # 所在显示器
        monitors = _enum_monitors()
        monvar = tk.StringVar(value=str(cfg.get("monitor", 0)))
        self._vars["monitor"] = monvar
        monvar.trace_add("write", self._on_change)
        monf = tk.Frame(top, bg="#f0f0f0")
        for i, m in enumerate(monitors):
            tk.Radiobutton(monf,
                           text="显示器 %d (%dx%d)" % (i + 1, m[2] - m[0], m[3] - m[1]),
                           variable=monvar, value=str(i), bg="#f0f0f0",
                           font=("Segoe UI", 9)).pack(anchor="w")
        self._row(top, r, "所在显示器", monf); r += 1

        self._spin(top, r, "隐藏条宽度(px)", "hidden_width", 2, 30); r += 1
        self._spin(top, r, "展开延迟(ms)", "show_delay_ms", 0, 5000, 50); r += 1
        self._spin(top, r, "收起延迟(ms)", "hide_delay_ms", 0, 5000, 50); r += 1
        self._spin(top, r, "每行应用数", "columns", 1, 10); r += 1
        self._spin(top, r, "面板宽度(px)", "dock_width", 120, 800, 10); r += 1
        self._spin(top, r, "面板高度(px)", "dock_height", 120, 1200, 10); r += 1
        self._spin(top, r, "圆角半径(px)", "corner_radius", 0, 40, 2); r += 1

        # 圆角实现方式
        cmode = tk.StringVar(value=str(cfg.get("corner_mode", "dwm")))
        self._vars["corner_mode"] = cmode
        cmode.trace_add("write", self._on_change)
        cmf = tk.Frame(top, bg="#f0f0f0")
        for cval, clab in (("dwm", "系统原生(高清)"), ("region", "经典裁剪"),
                           ("none", "关闭圆角")):
            tk.Radiobutton(cmf, text=clab, variable=cmode, value=cval,
                           bg="#f0f0f0", font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
        self._row(top, r, "圆角方式", cmf); r += 1
        tk.Label(top, text="提示：系统原生使用 DWM 圆角无锯齿；经典裁剪在部分系统上有锯齿",
                 bg="#f0f0f0", fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=r, column=0, columnspan=2, sticky="w",
                        padx=(10, 10), pady=(0, 4)); r += 1

        # 图标 / 文字尺寸
        self._spin(top, r, "图标大小(px)", "icon_size", 0, 512, 4); r += 1
        tk.Label(top, text="提示：0 表示按格子自动计算",
                 bg="#f0f0f0", fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=r, column=0, columnspan=2, sticky="w",
                        padx=(10, 10), pady=(0, 4)); r += 1
        self._spin(top, r, "图标边距(px)", "icon_margin", 0, 100, 2); r += 1
        self._spin(top, r, "文字大小(px)", "text_size", 6, 24, 1); r += 1

        # 图标圆角 / 状态点 / 点击空白收起
        icorner = tk.BooleanVar(value=bool(cfg.get("icon_corner", True)))
        self._vars["icon_corner"] = icorner
        icorner.trace_add("write", self._on_change)
        cbs = tk.BooleanVar(value=bool(cfg.get("click_blank_shrink", True)))
        self._vars["click_blank_shrink"] = cbs
        cbs.trace_add("write", self._on_change)
        shwtx = tk.BooleanVar(value=bool(cfg.get("show_text", True)))
        self._vars["show_text"] = shwtx
        shwtx.trace_add("write", self._on_change)
        chkf2 = tk.Frame(top, bg="#f0f0f0")
        tk.Checkbutton(chkf2, text="图标圆角", variable=icorner, bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        tk.Checkbutton(chkf2, text="点空白处收起", variable=cbs, bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        tk.Checkbutton(chkf2, text="显示文字", variable=shwtx, bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left")
        self._row(top, r, "图标与交互", chkf2); r += 1

        # 颜色主题（auto/light/dark 会覆盖下方自定义颜色）
        theme = tk.StringVar(value=str(cfg.get("theme", "auto")))
        self._vars["theme"] = theme
        theme.trace_add("write", self._on_change)
        themef = tk.Frame(top, bg="#f0f0f0")
        for tval, tlab in (("auto", "跟随系统"), ("light", "浅色"),
                           ("dark", "深色"), ("custom", "自定义")):
            tk.Radiobutton(themef, text=tlab, variable=theme, value=tval,
                           bg="#f0f0f0", font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
        self._row(top, r, "颜色主题", themef); r += 1
        tk.Label(top, text="提示：主题非「自定义」时，下方颜色将被主题色板覆盖",
                 bg="#f0f0f0", fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=r, column=0, columnspan=2, sticky="w",
                        padx=(10, 10), pady=(0, 4)); r += 1

        self._color(top, r, "背景颜色", "bg_color"); r += 1
        self._color(top, r, "隐藏条颜色", "hidden_color"); r += 1
        self._color(top, r, "文字颜色", "text_color"); r += 1

        # 半透明
        alphavar = tk.DoubleVar(value=float(cfg.get("alpha", 1.0)))
        self._vars["alpha"] = alphavar
        alphavar.trace_add("write", self._on_change)
        alphaf = tk.Frame(top, bg="#f0f0f0")
        sc = tk.Scale(alphaf, from_=0.5, to=1.0, resolution=0.05, orient="horizontal",
                      variable=alphavar, length=140, showvalue=True, bg="#f0f0f0",
                      highlightthickness=0, font=("Segoe UI", 8))
        sc.pack()
        self._row(top, r, "面板透明度", alphaf); r += 1

        # 开关
        topmost = tk.BooleanVar(value=bool(cfg.get("topmost", True)))
        self._vars["topmost"] = topmost
        topmost.trace_add("write", self._on_change)
        autostart = tk.BooleanVar(value=bool(cfg.get("autostart", False)))
        self._vars["autostart"] = autostart
        autostart.trace_add("write", self._on_change)
        starthid = tk.BooleanVar(value=bool(cfg.get("start_hidden", False)))
        self._vars["start_hidden"] = starthid
        starthid.trace_add("write", self._on_change)
        chkf = tk.Frame(top, bg="#f0f0f0")
        tk.Checkbutton(chkf, text="置顶显示", variable=topmost, bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        tk.Checkbutton(chkf, text="最小化运行", variable=starthid, bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        tk.Checkbutton(chkf, text="开机自启动", variable=autostart, bg="#f0f0f0",
                       font=("Segoe UI", 9)).pack(side="left")
        self._row(top, r, "开关选项", chkf); r += 1

        # 按钮
        tk.Label(self.win, text="所有改动实时生效，保存后写入配置；撤销可回到打开时的设置",
                 bg="#f0f0f0", fg="#888888", font=("Segoe UI", 8)
                 ).pack(fill="x", padx=10)
        btns = tk.Frame(self.win, bg="#f0f0f0")
        btns.pack(fill="x", pady=(6, 12))
        tk.Button(btns, text="保存并应用", width=12, command=self._save,
                  font=("Segoe UI", 9)).pack(side="right", padx=(0, 12))
        tk.Button(btns, text="撤销", width=10, command=self._undo,
                  font=("Segoe UI", 9)).pack(side="right", padx=(0, 6))
        tk.Button(btns, text="恢复默认", width=10, command=self._defaults,
                  font=("Segoe UI", 9)).pack(side="right", padx=(0, 6))
        tk.Button(btns, text="取消", width=10, command=self.win.destroy,
                  font=("Segoe UI", 9)).pack(side="right", padx=(0, 6))

    def _collect(self):
        """从控件读取值，返回新配置 dict。"""
        cfg = dict(self.cfg)
        for k, v in self._vars.items():
            try:
                if isinstance(v, tk.BooleanVar):
                    cfg[k] = bool(v.get())
                elif isinstance(v, tk.DoubleVar):
                    cfg[k] = round(float(v.get()), 2)
                else:
                    s = str(v.get()).strip()
                    if k == "corner_radius":
                        cfg[k] = max(0, min(40, int(s or 0)))
                    elif k == "icon_size":
                        cfg[k] = max(0, min(512, int(s or 0)))
                    elif k == "icon_margin":
                        cfg[k] = max(0, min(100, int(s or 0)))
                    elif k == "text_size":
                        cfg[k] = max(6, min(24, int(s or 8)))
                    elif k in ("hidden_width", "show_delay_ms", "hide_delay_ms",
                               "columns", "dock_width", "dock_height"):
                        cfg[k] = max(1, int(s or 0))
                    else:
                        cfg[k] = s
            except Exception:
                pass
        return cfg

    def _save(self):
        try:
            self.on_save(self._collect())
        finally:
            try:
                self.win.destroy()
            except Exception:
                pass

    def _undo(self):
        """撤销：把控件重置为打开面板时的配置并立即应用（不落盘）。"""
        for k, v in self._vars.items():
            try:
                v.set(self._orig_cfg.get(k, v.get()))
            except Exception:
                pass
        if self.on_live:
            try:
                self.on_live(dict(self._orig_cfg))
            except Exception:
                pass

    def _defaults(self):
        """恢复默认：把控件重置为出厂默认并立即应用（不落盘，保留应用列表）。"""
        for k, v in self._vars.items():
            try:
                v.set(DEFAULT_CONFIG.get(k, v.get()))
            except Exception:
                pass
        if self.on_live:
            try:
                ncfg = dict(self.cfg)
                ncfg.update({k2: v2 for k2, v2 in DEFAULT_CONFIG.items()
                             if k2 != "apps"})
                self.on_live(ncfg)
            except Exception:
                pass


def main():
    import traceback
    if getattr(sys, "frozen", False):
        _base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        _base_dir = os.path.dirname(os.path.abspath(__file__))
    _log_path = os.path.join(_base_dir, "edge_dock_err.log")
    def _log_exc(exc):
        try:
            with open(_log_path, "a", encoding="utf-8") as fh:
                fh.write("==== %s ====\n%s\n" % (__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"), exc))
        except Exception:
            pass
    sys.excepthook = lambda t, v, tb: _log_exc("".join(traceback.format_exception(t, v, tb)))
    cfg_path = os.path.join(_base_dir, "config.json")
    app = EdgeDock(cfg_path)
    try:
        app.root.report_callback_exception = lambda t, v, tb: _log_exc("TK-CALLBACK: " + "".join(traceback.format_exception(t, v, tb)))
    except Exception:
        pass
    app.root.mainloop()


if __name__ == "__main__":
    main()
