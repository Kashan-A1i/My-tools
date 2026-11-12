# My Tools

A collection of lightweight Windows desktop productivity utilities built with Python and PyQt6.

---

## Projects Included

| Project | Description | Entrypoint |
| :--- | :--- | :--- |
| **[File converter](./File%20converter/)** | **OmniConvert Pro** — Bulk file & presentation converter (`PPT/PPTX`, `PDF`, `DOC/DOCX`, `XLS/XLSX`, `CSV`, `JSON`, `MD`, `HTML`, `TXT`, and images) with multi-file PDF merging. | `File converter/app.py` (`Run_Converter.bat`) |
| **[Helper](./Helper/)** | **Ambient Screen-Reading & HUD Overlay Assistant** — On-demand screen-reading question analyzer with a capture-excluded, click-through HUD triggered via trackpad hot corners. | `Helper/main.py` (`run.bat`) |

---

## Quick Start

### 1. File Converter (OmniConvert Pro)

```powershell
cd "File converter"
pip install -r requirements.txt
python app.py
```
Or double-click `File converter\Run_Converter.bat`.

### 2. Helper (Ambient Screen-Reading & HUD Assistant)

```powershell
cd "Helper"
pip install -r requirements.txt
copy .env.example .env
# Add your GEMINI_API_KEY or OPENAI_API_KEY in .env (or config.json)
python main.py
```
Or double-click `Helper\run.bat`.
