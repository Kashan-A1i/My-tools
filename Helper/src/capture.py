import io
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageGrab

try:
    import mss
except ImportError:
    mss = None

from src.win32_utils import get_active_window_rect


@dataclass
class CapturedFrame:
    image: Image.Image
    png_bytes: bytes
    dhash: int
    diff_score: float
    forced: bool
    timestamp: float
    region: Dict[str, int]


class ScreenDiffer:
    """
    Fast perceptual hash (64-bit dHash) + downsampled L1 pixel diff checker.
    Prevents redundant OCR/LLM calls when screen content is static or only has minor cursor blink.
    """

    def __init__(self, threshold: float = 0.035) -> None:
        self.threshold = float(threshold)
        self._last_thumb: Optional[np.ndarray] = None
        self._last_dhash: Optional[int] = None

    @staticmethod
    def _compute_signature(img: Image.Image) -> Tuple[np.ndarray, int]:
        # Convert to 64x64 grayscale thumbnail for normalized L1 diff
        gray = img.convert("L")
        thumb = np.asarray(gray.resize((64, 64), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0

        # 9x8 resize for 64-bit horizontal gradient dHash
        dhash_arr = np.asarray(gray.resize((9, 8), Image.Resampling.BILINEAR), dtype=np.uint8)
        diff_bits = dhash_arr[:, 1:] > dhash_arr[:, :-1]
        dhash_val = 0
        for bit in diff_bits.flatten():
            dhash_val = (dhash_val << 1) | int(bit)

        return thumb, dhash_val

    def has_changed(self, img: Image.Image, force: bool = False) -> Tuple[bool, float, int]:
        thumb, dhash_val = self._compute_signature(img)

        if force or self._last_thumb is None or self._last_dhash is None:
            self._last_thumb = thumb
            self._last_dhash = dhash_val
            return True, 1.0, dhash_val

        # Mean absolute pixel difference across 64x64 grid
        l1_diff = float(np.mean(np.abs(thumb - self._last_thumb)))

        # Hamming distance of 64-bit dHash normalized to [0, 1]
        hamming = bin(dhash_val ^ self._last_dhash).count("1") / 64.0

        # Combined change score
        combined_score = max(l1_diff, hamming * 0.5)

        if combined_score >= self.threshold:
            self._last_thumb = thumb
            self._last_dhash = dhash_val
            return True, combined_score, dhash_val

        return False, combined_score, dhash_val

    def reset(self) -> None:
        self._last_thumb = None
        self._last_dhash = None


class ScreenCapturer:
    """Captures active window, custom region, or full primary monitor with low latency."""

    def __init__(self, capture_cfg: Dict[str, Any]) -> None:
        self.cfg = capture_cfg
        self.differ = ScreenDiffer(threshold=capture_cfg.get("diff_threshold", 0.035))
        self.max_dim = int(capture_cfg.get("max_image_dimension", 1280))

    def _resolve_target_region(self, sct_monitors: list) -> Dict[str, int]:
        mode = str(self.cfg.get("mode", "active_window")).lower()
        primary = sct_monitors[1] if len(sct_monitors) > 1 else sct_monitors[0]

        if mode == "region":
            reg = self.cfg.get("region", {})
            return {
                "left": int(reg.get("left", primary["left"])),
                "top": int(reg.get("top", primary["top"])),
                "width": max(100, int(reg.get("width", 1200))),
                "height": max(100, int(reg.get("height", 800))),
            }

        if mode == "active_window":
            rect = get_active_window_rect()
            if rect:
                # Clamp to sensible screen bounds
                left = max(primary["left"], rect["left"])
                top = max(primary["top"], rect["top"])
                right = min(primary["left"] + primary["width"], rect["left"] + rect["width"])
                bottom = min(primary["top"] + primary["height"], rect["top"] + rect["height"])
                if right - left > 120 and bottom - top > 120:
                    return {
                        "left": left,
                        "top": top,
                        "width": right - left,
                        "height": bottom - top,
                    }

        return {
            "left": int(primary["left"]),
            "top": int(primary["top"]),
            "width": int(primary["width"]),
            "height": int(primary["height"]),
        }

    def grab_frame(self, force: bool = False) -> Optional[CapturedFrame]:
        """Grab screen frame and return a CapturedFrame if changed (or if force=True)."""
        if mss is not None:
            try:
                with mss.mss() as sct:
                    region = self._resolve_target_region(sct.monitors)
                    try:
                        raw = sct.grab(region)
                    except Exception:
                        primary = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                        region = {
                            "left": int(primary["left"]),
                            "top": int(primary["top"]),
                            "width": int(primary["width"]),
                            "height": int(primary["height"]),
                        }
                        raw = sct.grab(region)
                    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:
                return None
        else:
            rect = get_active_window_rect() if self.cfg.get("mode") == "active_window" else None
            img = None
            region = {"left": 0, "top": 0, "width": 0, "height": 0}
            if rect:
                left = max(0, rect["left"])
                top = max(0, rect["top"])
                right = rect["left"] + rect["width"]
                bottom = rect["top"] + rect["height"]
                if right - left > 80 and bottom - top > 80:
                    try:
                        img = ImageGrab.grab(bbox=(left, top, right, bottom))
                        region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
                    except OSError:
                        img = None
            if img is None:
                try:
                    img = ImageGrab.grab()
                    region = {"left": 0, "top": 0, "width": img.width, "height": img.height}
                except OSError:
                    return None

        changed, diff_score, dhash = self.differ.has_changed(img, force=force)
        if not changed:
            return None

        # Downscale large Retina/4K captures for faster OCR & lower API payload size
        if max(img.width, img.height) > self.max_dim:
            scale = self.max_dim / float(max(img.width, img.height))
            new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return CapturedFrame(
            image=img,
            png_bytes=buf.getvalue(),
            dhash=dhash,
            diff_score=diff_score,
            forced=force,
            timestamp=time.time(),
            region=region,
        )
