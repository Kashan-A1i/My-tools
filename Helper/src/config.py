import json
import os
from pathlib import Path
from typing import Any, Dict

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "provider": "gemini",
        "gemini_api_key": "",
        "gemini_model": "gemini-3.6-flash",
        "gemini_fallback_models": [
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-flash-latest",
        ],
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "max_tokens": 400,
        "temperature": 0.1,
        "timeout_seconds": 15.0,
    },
    "ocr": {
        "engine": "rapidocr",  # "rapidocr" | "auto" | "vision" | "tesseract" | "winocr"
        "tesseract_cmd": "tesseract",
        "min_question_length": 6,
        "require_question_pattern": False,
    },
    "capture": {
        "mode": "full_screen",  # "full_screen" | "active_window" | "region"
        "region": {
            "left": 100,
            "top": 100,
            "width": 1200,
            "height": 800,
        },
        "max_image_dimension": 1440,
    },
    "trigger": {
        "mode": "hot_corner",
        "capture_corner": "bottom_left",
        "toggle_corner": "top_left",
        "dwell_seconds": 1.2,
        "corner_size_px": 28,
        "poll_interval_ms": 80,
        "cooldown_seconds": 2.5,
    },
    "overlay": {
        "width": 260,
        "min_height": 28,
        "max_height": 240,
        "offset_x": 18,
        "offset_y": 14,
        "idle_opacity": 0.45,
        "active_opacity": 0.85,
        "idle_timeout_seconds": 12.0,
        "fade_duration_ms": 180,
        "exclude_from_capture": True,
        "text_color": "#F3F4F6",
        "bg_color": "rgba(20, 20, 24, 0.78)",
        "border_color": "rgba(255, 255, 255, 0.12)",
        "border_radius": 6,
        "enable_shadow": True,
        "font_size": 10,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_env_file(env_file: Path) -> None:
    """Load key=value pairs from .env file using python-dotenv or built-in fallback."""
    if not env_file.exists():
        return
    if load_dotenv is not None:
        load_dotenv(env_file)
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """Load configuration from config.json and apply .env / environment overrides."""
    base_dir = Path(__file__).resolve().parent.parent
    env_file = base_dir / ".env"
    _load_env_file(env_file)

    cfg_file = Path(config_path)
    if not cfg_file.is_absolute():
        cfg_file = base_dir / cfg_file

    user_cfg: Dict[str, Any] = {}
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
        except Exception as exc:
            print(f"[Config] Warning: Could not parse {cfg_file}: {exc}")

    config = _deep_merge(DEFAULT_CONFIG, user_cfg)

    # Environment variable overrides
    if os.getenv("GEMINI_API_KEY"):
        config["llm"]["gemini_api_key"] = os.getenv("GEMINI_API_KEY", "").strip()
    if os.getenv("OPENAI_API_KEY"):
        config["llm"]["openai_api_key"] = os.getenv("OPENAI_API_KEY", "").strip()
    if os.getenv("LLM_PROVIDER"):
        config["llm"]["provider"] = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

    return config
