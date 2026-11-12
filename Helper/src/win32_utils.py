import ctypes
from ctypes import wintypes
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# Win32 Extended Window Styles
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

# Window Display Affinity (hides window from screenshots / screen capture)
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+ and Windows 11

# DWM Extended Frame Bounds
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# System Metrics
SM_CXSCREEN = 0
SM_CYSCREEN = 1


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def apply_overlay_window_styles(
    hwnd: int,
    exclude_from_capture: bool = True,
    enable_blur: bool = False,
) -> None:
    """
    Configure native Win32 flags on the HUD window:
    - Click-through (WS_EX_TRANSPARENT | WS_EX_LAYERED)
    - Hidden from Alt+Tab / taskbar (WS_EX_TOOLWINDOW)
    - Does not steal focus (WS_EX_NOACTIVATE)
    - Excluded from screen capture (WDA_EXCLUDEFROMCAPTURE)
    """
    if not IS_WINDOWS or not hwnd:
        return

    try:
        user32 = ctypes.windll.user32

        # 1. Set click-through, toolwindow, no-activate extended styles
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = (
            ex_style
            | WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOOLWINDOW
            | WS_EX_TOPMOST
            | WS_EX_NOACTIVATE
        )
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

        # 2. Exclude overlay from screen capture so it never captures itself
        if exclude_from_capture:
            ok = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if not ok:
                user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)

        # 3. Only enable DWM blur if explicitly requested (disabled for pure transparent background)
        if enable_blur:
            _enable_dwm_blur(hwnd)
    except Exception as exc:
        print(f"[Win32] Warning applying window styles: {exc}")


def _enable_dwm_blur(hwnd: int) -> None:
    """Enable Windows 10/11 DWM blur behind the overlay."""
    if not IS_WINDOWS:
        return
    try:
        user32 = ctypes.windll.user32
        set_comp_attr = getattr(user32, "SetWindowCompositionAttribute", None)
        if not set_comp_attr:
            return

        policy = ACCENT_POLICY()
        policy.AccentState = 3
        policy.AccentFlags = 0
        policy.GradientColor = 0x00000000
        policy.AnimationId = 0

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)

        set_comp_attr(hwnd, ctypes.byref(data))
    except Exception:
        pass


def get_active_window_rect() -> Optional[Dict[str, int]]:
    """Return bounding box {'left', 'top', 'width', 'height'} of the foreground window."""
    if not IS_WINDOWS:
        return None

    try:
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        rect = wintypes.RECT()
        res = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if res != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None

        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 80 or height <= 80:
            return None

        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(width),
            "height": int(height),
        }
    except Exception:
        return None


def get_cursor_and_screen_size() -> Optional[Tuple[int, int, int, int]]:
    """Return (cursor_x, cursor_y, screen_width, screen_height)."""
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None
        screen_w = int(user32.GetSystemMetrics(SM_CXSCREEN))
        screen_h = int(user32.GetSystemMetrics(SM_CYSCREEN))
        if screen_w <= 0 or screen_h <= 0:
            return None
        return (int(pt.x), int(pt.y), screen_w, screen_h)
    except Exception:
        return None


def is_point_in_corner(
    x: int,
    y: int,
    screen_w: int,
    screen_h: int,
    corner: str,
    corner_size: int = 28,
) -> bool:
    """Check whether (x, y) lies inside the specified screen corner zone."""
    corner = (corner or "").strip().lower()
    if corner == "top_left":
        return x <= corner_size and y <= corner_size
    if corner == "bottom_left":
        return x <= corner_size and y >= (screen_h - corner_size)
    if corner == "top_right":
        return x >= (screen_w - corner_size) and y <= corner_size
    if corner == "bottom_right":
        return x >= (screen_w - corner_size) and y >= (screen_h - corner_size)
    return False


class TrackpadHotCornerMonitor:
    """
    Lightweight, zero-hook trackpad/cursor hot-corner dwell detector.
    Resting the cursor in `capture_corner` (default: bottom_left) for `dwell_seconds` (1.2s)
    triggers a screenshot & question analysis without touching any keyboard keys or clicking.
    Resting the cursor in `toggle_corner` (default: top_left) toggles the minimal overlay.
    """

    def __init__(
        self,
        trigger_cfg: Dict[str, Any],
        on_capture: Optional[Callable[[], None]] = None,
        on_toggle: Optional[Callable[[], None]] = None,
    ) -> None:
        self.cfg = trigger_cfg
        self.capture_corner = str(trigger_cfg.get("capture_corner", "bottom_left")).lower()
        self.toggle_corner = str(trigger_cfg.get("toggle_corner", "top_left")).lower()
        self.dwell_seconds = max(0.3, float(trigger_cfg.get("dwell_seconds", 1.2)))
        self.corner_size_px = max(8, int(trigger_cfg.get("corner_size_px", 28)))
        self.poll_interval = max(0.03, float(trigger_cfg.get("poll_interval_ms", 80)) / 1000.0)
        self.cooldown_seconds = max(0.5, float(trigger_cfg.get("cooldown_seconds", 2.5)))

        self.on_capture = on_capture
        self.on_toggle = on_toggle

        self._active_corner: Optional[str] = None
        self._entered_at: float = 0.0
        self._fired_for_current_visit: bool = False
        self._last_fire_time: float = 0.0

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not IS_WINDOWS or self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="TrackpadHotCornerLoop",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def step(
        self,
        x: int,
        y: int,
        screen_w: int,
        screen_h: int,
        now: Optional[float] = None,
    ) -> Optional[str]:
        """
        Process a single cursor position reading and return 'capture' or 'toggle'
        if the dwell threshold was just reached.
        """
        now_t = time.monotonic() if now is None else now

        detected: Optional[str] = None
        if self.capture_corner and is_point_in_corner(
            x, y, screen_w, screen_h, self.capture_corner, self.corner_size_px
        ):
            detected = "capture"
        elif self.toggle_corner and is_point_in_corner(
            x, y, screen_w, screen_h, self.toggle_corner, self.corner_size_px
        ):
            detected = "toggle"

        if detected is None:
            self._active_corner = None
            self._entered_at = 0.0
            self._fired_for_current_visit = False
            return None

        if detected != self._active_corner:
            self._active_corner = detected
            self._entered_at = now_t
            self._fired_for_current_visit = False
            return None

        if self._fired_for_current_visit:
            return None

        if (now_t - self._entered_at) >= self.dwell_seconds:
            if (now_t - self._last_fire_time) >= self.cooldown_seconds:
                self._fired_for_current_visit = True
                self._last_fire_time = now_t
                return detected

        return None

    def _poll_loop(self) -> None:
        while self._running:
            info = get_cursor_and_screen_size()
            if info is not None:
                x, y, sw, sh = info
                action = self.step(x, y, sw, sh)
                if action == "capture" and self.on_capture:
                    try:
                        self.on_capture()
                    except Exception as exc:
                        print(f"[TrackpadTrigger] Capture callback error: {exc}")
                elif action == "toggle" and self.on_toggle:
                    try:
                        self.on_toggle()
                    except Exception as exc:
                        print(f"[TrackpadTrigger] Toggle callback error: {exc}")
            time.sleep(self.poll_interval)

