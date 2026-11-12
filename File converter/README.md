# OmniConvert Pro — Bulk File Converter

A clean, fast desktop application built with **PyQt6** for bulk converting presentations, documents, spreadsheets, and images on Windows.

---

## Supported Formats

- **Presentations:** `.pptx`, `.ppt`
- **Documents:** `.pdf`, `.docx`, `.doc`, `.rtf`, `.txt`, `.md`, `.html`
- **Spreadsheets & Data:** `.xlsx`, `.xls`, `.csv`, `.json`
- **Images:** `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tiff`
- **PDF Merging:** Combine multiple files of any supported format into a single merged `.pdf`.

---

## Setup & Usage

### 1. Install Dependencies

```powershell
pip install -r requirements.txt
```

*(Note: Converting `.ppt/.pptx`, `.doc/.docx`, and `.xls/.xlsx` to PDF or legacy Office formats uses Microsoft Office COM automation via `pywin32` when Office is installed, plus pure-Python engines `PyMuPDF`, `pdf2docx`, `python-pptx`, `python-docx`, `openpyxl`, and `Pillow`.)*

### 2. Launch the Application

**Option A — Double-click `Run_Converter.bat`**

**Option B — From Terminal:**
```powershell
python app.py
```
