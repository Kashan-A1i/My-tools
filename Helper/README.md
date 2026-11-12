# Ambient Screen-Reading & HUD Overlay Assistant

A lightweight, minimal Python/PyQt6 desktop application for on-demand screen-reading question analysis and ambient click-through HUD assistance on Windows.

---

## 1. Architecture & Key Features

- **Minimal Footprint Stack:** Pure Python (`PyQt6` + native `ctypes` Win32/DWM bindings + `mss` + `Pillow`/`numpy`) for low CPU/RAM overhead and fast capture latency.
- **Capture-Excluded & Click-Through HUD:**
  - Uses Win32 `WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` so mouse clicks pass seamlessly through to underlying apps and the overlay never steals focus or clutters `Alt+Tab`.
  - Uses `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` so the HUD is **100% invisible to screen captures**—preventing feedback loops where the assistant captures its own overlay.
  - Anchored to the bottom-right corner of the primary display with a subtle dark translucent pill and smooth opacity fade transitions.
- **Zero-Hook Trackpad Hot-Corner Trigger:**
  - No keyboard hooks required: resting the cursor in the **bottom-left corner** (`capture_corner`) for `1.2s` triggers an on-demand screen capture and analysis.
  - Resting the cursor in the **top-left corner** (`toggle_corner`) for `1.2s` hides or reveals the HUD overlay.
- **Hybrid OCR & Vision Question Extraction:**
  - Supports offline OCR engines (`rapidocr-onnxruntime`, `winocr`, `pytesseract`) as well as direct multimodal **Vision API** analysis (`Gemini Flash/Lite` or `GPT-4o-mini`).
  - Automatically parses multi-line question stems and multiple-choice options into a structured `QuestionPayload`.

---

## 2. Directory Structure

```text
Helper/
├── main.py                # Application entrypoint, worker thread, tray menu & hot-corner wiring
├── config.json            # Runtime configuration (models, OCR, capture region, hot corners, HUD)
├── .env.example           # Environment variable template for API keys
├── requirements.txt       # Python dependencies
├── run.bat                # Background launcher (pythonw)
├── README.md              # Setup & usage documentation
└── src/
    ├── __init__.py
    ├── config.py          # Config loader merging config.json + .env overrides
    ├── win32_utils.py     # Win32 click-through, WDA_EXCLUDEFROMCAPTURE & TrackpadHotCornerMonitor
    ├── capture.py         # Non-blocking MSS screen grabber + dHash / L1 pixel diff engine
    ├── extractor.py       # Multi-backend OCR runner + regex question/MCQ structure parser
    ├── reasoning.py       # Gemini Flash/Lite & OpenAI low-latency REST client with model failover
    └── overlay.py         # PyQt6 frameless, transparent, always-on-top HUD
```

---

## 3. Setup & Launch

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 2. Configure API Key
Copy `.env.example` to `.env` and set your API key (`.env` is git-ignored):
```ini
GEMINI_API_KEY=your_gemini_api_key_here
LLM_PROVIDER=gemini
```
*(Alternatively, you can set `gemini_api_key` or `openai_api_key` in `config.json`.)*

### 3. Launch the Application

**Option A — Double-click `run.bat`:**
Runs `pythonw main.py` in the background with no console window.

**Option B — From PowerShell / Terminal:**
```powershell
python main.py
```

### 4. Run End-to-End Pipeline Self-Test
```powershell
python main.py --self-test
```

---

## 4. Controls

| Trigger | Action | Description |
| :--- | :--- | :--- |
| **Bottom-Left Corner (`1.2s` dwell)** | **Capture & Analyze** | Captures the screen and runs OCR/LLM question analysis. |
| **Top-Left Corner (`1.2s` dwell)** | **Toggle Overlay HUD** | Hides or reveals the bottom-right HUD window. |
| **System Tray Icon (Right-Click)** | **Tray Menu** | View latest answers, trigger `Capture Now`, `Show / Hide Window`, or `Quit`. |
