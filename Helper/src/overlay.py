from typing import Any, Dict, List, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
)
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.win32_utils import apply_overlay_window_styles


class AmbientOverlayHUD(QWidget):
    """
    Ultra-minimal, click-through, capture-excluded HUD anchored to the bottom-right corner.
    Aesthetic: Translucent frosted/dark pill with subtle shadow and border, ensuring
    crisp legibility across both pure white and dark backgrounds.
    """

    def __init__(self, overlay_cfg: Dict[str, Any], trigger_cfg: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(None)
        self.cfg = overlay_cfg
        self.trigger_cfg = trigger_cfg or {}

        self.card_width = int(self.cfg.get("width", 260))
        self.min_height = int(self.cfg.get("min_height", 28))
        self.max_height = int(self.cfg.get("max_height", 240))
        self.offset_x = int(self.cfg.get("offset_x", 18))
        self.offset_y = int(self.cfg.get("offset_y", 14))
        self.idle_opacity = float(self.cfg.get("idle_opacity", 0.45))
        self.active_opacity = float(self.cfg.get("active_opacity", 0.85))
        self.idle_timeout_ms = int(float(self.cfg.get("idle_timeout_seconds", 12.0)) * 1000)
        self.fade_duration_ms = int(self.cfg.get("fade_duration_ms", 180))
        self.exclude_from_capture = bool(self.cfg.get("exclude_from_capture", True))
        self.text_color = str(self.cfg.get("text_color", "#F3F4F6"))
        self.bg_color = str(self.cfg.get("bg_color", "rgba(20, 20, 24, 0.78)"))
        self.border_color = str(self.cfg.get("border_color", "rgba(255, 255, 255, 0.12)"))
        self.border_radius = int(self.cfg.get("border_radius", 6))
        self.enable_shadow = bool(self.cfg.get("enable_shadow", True))
        self.font_size = int(self.cfg.get("font_size", 10))

        self._user_hidden = False
        self._styles_applied = False

        self._init_window_flags()
        self._build_ui()
        self._init_animations()
        self.reposition_bottom_right()

    def _init_window_flags(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(self.card_width)
        self.setMinimumHeight(self.min_height)
        self.setMaximumHeight(self.max_height)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(6, 4, 6, 4)
        root_layout.setSpacing(2)

        self.answer_label = QLabel("")
        self.answer_label.setWordWrap(True)
        self.answer_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.answer_label.setTextFormat(Qt.TextFormat.PlainText)
        self.answer_label.setFont(QFont("Segoe UI", self.font_size, QFont.Weight.Normal))
        self.answer_label.setMaximumWidth(self.card_width - 12)

        if self.enable_shadow:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(10)
            shadow.setColor(QColor(0, 0, 0, 140))
            shadow.setOffset(0, 2)
            self.answer_label.setGraphicsEffect(shadow)

        self._update_label_style(has_content=False)
        root_layout.addWidget(self.answer_label, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

    def _update_label_style(self, has_content: bool) -> None:
        """Apply a sleek translucent pill background when content is displayed."""
        if not has_content:
            self.answer_label.setStyleSheet("background: transparent; border: none;")
        else:
            style = (
                f"color: {self.text_color}; "
                f"background-color: {self.bg_color}; "
                f"border: 1px solid {self.border_color}; "
                f"border-radius: {self.border_radius}px; "
                f"padding: 5px 9px;"
            )
            self.answer_label.setStyleSheet(style)

    def _init_animations(self) -> None:
        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.opacity_anim.setDuration(self.fade_duration_ms)
        self.opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self._fade_to_idle)
        self.idle_timer.start(self.idle_timeout_ms)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._styles_applied:
            hwnd = int(self.winId())
            apply_overlay_window_styles(
                hwnd,
                exclude_from_capture=self.exclude_from_capture,
                enable_blur=False,
            )
            self._styles_applied = True
        self.reposition_bottom_right()

    def reposition_bottom_right(self) -> None:
        """Anchor the transparent text window to the bottom-right corner of the primary display."""
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        avail = screen.availableGeometry()
        w = self.width()
        h = max(self.min_height, min(self.height(), self.max_height))
        self.resize(w, h)

        x = avail.right() - w - self.offset_x
        y = avail.bottom() - h - self.offset_y
        self.move(x, y)

    def fade_to(self, target_opacity: float) -> None:
        self.opacity_anim.stop()
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(target_opacity)
        self.opacity_anim.start()

    def _fade_to_idle(self) -> None:
        if not self._user_hidden and self.isVisible():
            self.fade_to(self.idle_opacity)

    def wake_up(self) -> None:
        if self._user_hidden:
            return
        if not self.isVisible():
            self.show()
        self.fade_to(self.active_opacity)
        self.idle_timer.start(self.idle_timeout_ms)

    def toggle_visibility(self) -> bool:
        """Toggle HUD visibility with smooth fade transition."""
        self._user_hidden = not self._user_hidden
        if self._user_hidden:
            self.idle_timer.stop()
            self.opacity_anim.stop()
            self.hide()
        else:
            self.setWindowOpacity(0.0)
            self.show()
            hwnd = int(self.winId())
            apply_overlay_window_styles(
                hwnd,
                exclude_from_capture=self.exclude_from_capture,
                enable_blur=False,
            )
            self.reposition_bottom_right()
            self.fade_to(self.active_opacity)
            self.idle_timer.start(self.idle_timeout_ms)
        return not self._user_hidden

    def set_scanning_state(self, forced: bool = True) -> None:
        """Show a subtle monochrome indicator while capturing/analyzing."""
        if self._user_hidden:
            return
        self.answer_label.setText("...")
        self._update_label_style(has_content=True)
        self.reposition_bottom_right()
        self.wake_up()

    def update_lines(self, lines: List[str]) -> None:
        """Render minimal 'Q#: Answer' lines inside the contrast pill."""
        if self._user_hidden:
            return
        display_text = "\n".join(lines).strip() if lines else "—"
        self.answer_label.setText(display_text)
        self._update_label_style(has_content=bool(display_text))
        self.reposition_bottom_right()
        self.wake_up()

    def update_answer(
        self,
        question_summary: str = "",
        answer: str = "",
        rationale: str = "",
        latency_ms: int = 0,
        has_question: bool = True,
        error: Optional[str] = None,
        lines: Optional[List[str]] = None,
    ) -> None:
        """Render new answer(s) using only monochrome 'Q#: Answer' lines."""
        if self._user_hidden:
            return

        if lines is not None:
            self.update_lines(lines)
            return

        if error:
            self.update_lines([f"Err: {answer or error[:36]}"])
            return

        if not has_question and not answer:
            self.update_lines(["—"])
            return

        raw_lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
        self.update_lines(raw_lines)

