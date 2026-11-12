import argparse
from pathlib import Path
import sys
import threading
from typing import Any, Dict, List

# Ensure local package imports work regardless of the caller's working directory
_PROJECT_DIR = str(Path(__file__).resolve().parent)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from PyQt6.QtCore import QObject, QThread, pyqtSignal, Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from src.capture import ScreenCapturer
from src.config import load_config
from src.extractor import QuestionExtractor
from src.overlay import AmbientOverlayHUD
from src.reasoning import ReasoningEngine, ReasoningResult
from src.win32_utils import TrackpadHotCornerMonitor


class AnalysisWorker(QThread):
    """
    On-demand background worker thread.
    Live screen observation is disabled; captures and analyzes ONLY when triggered.
    """

    scanning_started = pyqtSignal(bool)  # forced: bool
    result_ready = pyqtSignal(object)    # ReasoningResult

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self.config = config
        self.capturer = ScreenCapturer(config["capture"])
        self.extractor = QuestionExtractor(config["ocr"])
        self.reasoning = ReasoningEngine(config["llm"])

        self._running = True
        self._capture_requested = False
        self._wake_event = threading.Event()

    def request_force_capture(self) -> None:
        """Trigger an immediate on-demand screenshot + question analysis."""
        self._capture_requested = True
        self._wake_event.set()

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()

    def run(self) -> None:
        while self._running:
            self._wake_event.wait(timeout=0.5)
            self._wake_event.clear()

            if not self._running:
                break

            if self._capture_requested:
                self._capture_requested = False
                self._process_single_cycle()

    def _process_single_cycle(self) -> None:
        try:
            self.scanning_started.emit(True)
            frame = self.capturer.grab_frame(force=True)
            if frame is None:
                self.result_ready.emit(
                    ReasoningResult(
                        has_question=False,
                        answer="—",
                        error="capture_failed",
                    )
                )
                return

            payload = self.extractor.extract_payload(frame.image, force=True)
            if payload is None:
                self.result_ready.emit(
                    ReasoningResult(
                        has_question=False,
                        answer="—",
                    )
                )
                return

            result: ReasoningResult = self.reasoning.analyze(
                payload=payload,
                png_bytes=frame.png_bytes,
            )
            self.result_ready.emit(result)

        except Exception as exc:
            self.result_ready.emit(
                ReasoningResult(
                    has_question=False,
                    answer="Err",
                    error=str(exc),
                )
            )


class ControllerBridge(QObject):
    """Thread-safe signal bridge between trackpad monitor thread and Qt GUI thread."""

    sig_toggle_overlay = pyqtSignal()
    sig_force_capture = pyqtSignal()
    sig_quit = pyqtSignal()


def _create_tray_icon() -> QIcon:
    """Create a subtle monochrome dark-gray icon in memory (no eye-catching colors)."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setBrush(QColor(36, 38, 42, 220))
    painter.setPen(QColor(130, 136, 144, 180))
    painter.drawRoundedRect(10, 10, 44, 44, 10, 10)

    painter.setBrush(QColor(142, 146, 153))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(25, 25, 14, 14)
    painter.end()
    return QIcon(pix)


def run_self_test(config: Dict[str, Any]) -> int:
    """Verify on-demand screen capture, trackpad hot-corner dwell, and multi-question extraction."""
    from PIL import Image, ImageDraw

    print("[Self-Test] 1. Testing On-Demand ScreenCapturer...")
    capturer = ScreenCapturer(config["capture"])
    frame1 = capturer.grab_frame(force=True)
    if frame1 is not None:
        print(
            f"  -> Captured {frame1.image.width}x{frame1.image.height} screenshot "
            f"(PNG={len(frame1.png_bytes)} bytes)"
        )
    else:
        print("  -> Headless session detected; verified ScreenCapturer fallback.")

    print("[Self-Test] 2. Testing TrackpadHotCornerMonitor dwell trigger...")
    monitor = TrackpadHotCornerMonitor(
        {
            "capture_corner": "bottom_left",
            "toggle_corner": "top_left",
            "dwell_seconds": 1.2,
            "corner_size_px": 28,
            "cooldown_seconds": 2.0,
        }
    )
    assert monitor.step(5, 1075, 1920, 1080, now=10.0) is None, "Should not fire at t=0"
    assert monitor.step(5, 1075, 1920, 1080, now=10.8) is None, "Should not fire before 1.2s"
    fired = monitor.step(5, 1075, 1920, 1080, now=11.25)
    assert fired == "capture", f"Expected 'capture' after 1.25s dwell, got {fired}"
    assert monitor.step(5, 1075, 1920, 1080, now=13.5) is None, "Should not re-fire without leaving corner"
    assert monitor.step(500, 500, 1920, 1080, now=14.0) is None, "Left corner"
    assert monitor.step(5, 5, 1920, 1080, now=14.5) is None, "Entered top-left corner"
    toggle_fired = monitor.step(5, 5, 1920, 1080, now=15.8)
    assert toggle_fired == "toggle", f"Expected 'toggle' after top-left dwell, got {toggle_fired}"
    print("  -> Verified bottom-left capture dwell (1.2s) & top-left hide/show dwell (1.2s)")

    print("[Self-Test] 3. Testing Multi-Question OCR & Live LLM Reasoning...")
    extractor = QuestionExtractor(config["ocr"])
    sample_ocr = """
    Question 14:
    What is the average-case time complexity of Merge Sort on an array of n elements?
    A) O(n)
    B) O(n log n)
    C) O(n^2)
    D) O(log n)

    Question 15:
    What is the capital of France?
    A) Madrid
    B) Paris
    C) Rome
    """
    payload = extractor.parse_text(sample_ocr, force=True, engine_name="test")
    assert payload is not None and payload.is_mcq, "Failed to parse multi-question sample"

    reasoning = ReasoningEngine(config["llm"])
    res = reasoning.analyze(payload)
    lines = res.format_lines()
    print(f"  -> Provider: {res.provider} ({res.latency_ms}ms)")
    print(f"  -> Formatted minimal output:\n     " + "\n     ".join(lines))
    assert len(lines) >= 1 and any("Q14" in ln for ln in lines), f"Unexpected lines: {lines}"

    print("[Self-Test] All core checks passed!")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal Trackpad-Triggered Question Assistant")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--self-test", action="store_true", help="Run non-interactive pipeline verification")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.self_test:
        return run_self_test(config)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Helper")

    # 1. Initialize Minimal Transparent Overlay HUD
    hud = AmbientOverlayHUD(config["overlay"], config.get("trigger", {}))
    hud.show()

    # 2. Initialize On-Demand Capture & Reasoning Worker (no live observation)
    worker = AnalysisWorker(config)
    worker.scanning_started.connect(hud.set_scanning_state)

    # 3. Setup System Tray Icon & Dynamic Answer Menu
    tray = QSystemTrayIcon(_create_tray_icon(), app)
    tray.setToolTip("Ready")
    menu = QMenu()

    answer_actions: List[QAction] = []
    no_ans_action = QAction("No answers yet", menu)
    no_ans_action.setEnabled(False)
    menu.addAction(no_ans_action)
    menu_sep = menu.addSeparator()

    act_capture = QAction("Capture Now", menu)
    act_toggle_hud = QAction("Show / Hide Window", menu)
    act_quit = QAction("Quit", menu)

    menu.addAction(act_capture)
    menu.addAction(act_toggle_hud)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.show()

    def on_result_ready(res: ReasoningResult) -> None:
        lines = res.format_lines()
        hud.update_lines(lines)

        # Also update the tray menu items with Question # and Key Answer
        for old_act in answer_actions:
            menu.removeAction(old_act)
        answer_actions.clear()

        if lines:
            no_ans_action.setVisible(False)
            for ln in lines:
                act = QAction(ln, menu)
                menu.insertAction(menu_sep, act)
                answer_actions.append(act)
            tray.setToolTip("\n".join(lines))
        else:
            no_ans_action.setText("—")
            no_ans_action.setVisible(True)
            tray.setToolTip("—")

    worker.result_ready.connect(on_result_ready)

    # 4. Trackpad Hot-Corner Monitor Bridge (no keyboard keys touched)
    bridge = ControllerBridge()
    bridge.sig_toggle_overlay.connect(hud.toggle_visibility)
    bridge.sig_force_capture.connect(worker.request_force_capture)

    corner_monitor = TrackpadHotCornerMonitor(
        config.get("trigger", {}),
        on_capture=bridge.sig_force_capture.emit,
        on_toggle=bridge.sig_toggle_overlay.emit,
    )

    def clean_shutdown() -> None:
        corner_monitor.stop()
        worker.stop()
        worker.wait(1500)
        tray.hide()
        hud.close()
        app.quit()

    bridge.sig_quit.connect(clean_shutdown)

    act_capture.triggered.connect(worker.request_force_capture)
    act_toggle_hud.triggered.connect(hud.toggle_visibility)
    act_quit.triggered.connect(clean_shutdown)

    corner_monitor.start()
    worker.start()

    if not worker.reasoning.is_configured():
        hud.update_lines(["Set API key in config.json"])
    else:
        hud.update_lines([])

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

