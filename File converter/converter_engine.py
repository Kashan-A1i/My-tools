"""
Core Conversion Engine for OmniConvert Pro
Supports bulk interconversion between:
- Presentations: PPT, PPTX
- Documents: PDF, DOC, DOCX, TXT, MD, HTML, RTF
- Spreadsheets: XLS, XLSX, CSV, JSON
- Images: PNG, JPG, JPEG, WEBP, BMP, TIFF
"""

import os
import sys
import csv
import json
import shutil
import tempfile
import traceback
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Callable

# Supported Input Categories and Extensions
FORMAT_CATEGORIES = {
    "Presentation": ["pptx", "ppt"],
    "Document": ["pdf", "docx", "doc", "txt", "md", "html", "rtf"],
    "Spreadsheet": ["xlsx", "xls", "csv", "json"],
    "Image": ["png", "jpg", "jpeg", "webp", "bmp", "tiff"]
}

ALL_INPUT_EXTENSIONS = [ext for exts in FORMAT_CATEGORIES.values() for ext in exts]

# Supported target conversions per input category/format
CONVERSION_MATRIX = {
    # Presentations
    "ppt":  ["pdf", "pptx", "png", "jpg", "txt"],
    "pptx": ["pdf", "ppt", "png", "jpg", "txt", "docx"],
    # PDF
    "pdf":  ["pptx", "docx", "txt", "png", "jpg", "html", "md"],
    # Word Documents
    "doc":  ["pdf", "docx", "txt", "html", "rtf", "md"],
    "docx": ["pdf", "doc", "txt", "html", "rtf", "md", "pptx"],
    "rtf":  ["pdf", "docx", "txt", "html"],
    # Plain Text / Web
    "txt":  ["pdf", "docx", "html", "md", "pptx"],
    "md":   ["pdf", "docx", "html", "txt", "pptx"],
    "html": ["pdf", "docx", "txt", "md"],
    # Spreadsheets & Data
    "xls":  ["pdf", "xlsx", "csv", "json", "html", "txt"],
    "xlsx": ["pdf", "xls", "csv", "json", "html", "txt"],
    "csv":  ["xlsx", "pdf", "json", "html", "txt", "md"],
    "json": ["csv", "xlsx", "txt", "html", "pdf"],
    # Images
    "png":  ["pdf", "pptx", "docx", "jpg", "webp", "bmp", "tiff"],
    "jpg":  ["pdf", "pptx", "docx", "png", "webp", "bmp", "tiff"],
    "jpeg": ["pdf", "pptx", "docx", "png", "webp", "bmp", "tiff"],
    "webp": ["pdf", "pptx", "docx", "png", "jpg", "bmp", "tiff"],
    "bmp":  ["pdf", "pptx", "docx", "png", "jpg", "webp", "tiff"],
    "tiff": ["pdf", "pptx", "docx", "png", "jpg", "webp", "bmp"],
}

ALL_TARGET_FORMATS = [
    ("pdf",  "PDF Document (.pdf)"),
    ("pptx", "PowerPoint Presentation (.pptx)"),
    ("docx", "Word Document (.docx)"),
    ("xlsx", "Excel Workbook (.xlsx)"),
    ("png",  "PNG Image(s) (.png)"),
    ("jpg",  "JPEG Image(s) (.jpg)"),
    ("webp", "WebP Image(s) (.webp)"),
    ("txt",  "Plain Text (.txt)"),
    ("md",   "Markdown Document (.md)"),
    ("html", "HTML Webpage (.html)"),
    ("csv",  "CSV Spreadsheet (.csv)"),
    ("json", "JSON Data (.json)"),
    ("rtf",  "Rich Text Format (.rtf)"),
    ("ppt",  "Legacy PowerPoint 97-2003 (.ppt)"),
    ("doc",  "Legacy Word 97-2003 (.doc)"),
    ("xls",  "Legacy Excel 97-2003 (.xls)"),
    ("bmp",  "Bitmap Image (.bmp)"),
    ("tiff", "TIFF Image (.tiff)"),
]


def get_common_targets(input_extensions: List[str]) -> List[Tuple[str, str]]:
    """Return target formats supported by at least one (or all) of the input extensions."""
    if not input_extensions:
        return ALL_TARGET_FORMATS
    valid_targets = set()
    for ext in input_extensions:
        ext_clean = ext.lower().lstrip(".")
        targets = CONVERSION_MATRIX.get(ext_clean, [])
        valid_targets.update(targets)
    if not valid_targets:
        return ALL_TARGET_FORMATS
    return [item for item in ALL_TARGET_FORMATS if item[0] in valid_targets]


def can_convert(src_ext: str, dst_ext: str) -> bool:
    src = src_ext.lower().lstrip(".")
    dst = dst_ext.lower().lstrip(".")
    if src == dst:
        return True
    return dst in CONVERSION_MATRIX.get(src, [])


class ConversionEngine:
    """
    Handles single and bulk file conversions.
    Maintains lazy-loaded COM instances per worker session for fast batch processing.
    """

    def __init__(self, dpi: int = 200):
        self.dpi = dpi
        self._ppt_app = None
        self._word_app = None
        self._excel_app = None
        self._com_initialized = False

    def __enter__(self):
        self.init_com()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup_com()

    def init_com(self):
        if not self._com_initialized:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                self._com_initialized = True
            except Exception:
                pass

    def cleanup_com(self):
        for app_attr in ("_ppt_app", "_word_app", "_excel_app"):
            app = getattr(self, app_attr, None)
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
                setattr(self, app_attr, None)
        if self._com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_initialized = False

    def _get_powerpoint(self):
        if self._ppt_app is None:
            import win32com.client
            self._ppt_app = win32com.client.DispatchEx("PowerPoint.Application")
            try:
                self._ppt_app.DisplayAlerts = 1  # ppAlertsNone
            except Exception:
                pass
        return self._ppt_app

    def _get_word(self):
        if self._word_app is None:
            import win32com.client
            self._word_app = win32com.client.DispatchEx("Word.Application")
            try:
                self._word_app.Visible = False
                self._word_app.DisplayAlerts = 0  # wdAlertsNone
            except Exception:
                pass
        return self._word_app

    def _get_excel(self):
        if self._excel_app is None:
            import win32com.client
            self._excel_app = win32com.client.DispatchEx("Excel.Application")
            try:
                self._excel_app.Visible = False
                self._excel_app.DisplayAlerts = False
            except Exception:
                pass
        return self._excel_app

    def convert_file(self, src_path: str, dst_ext: str, output_dir: Optional[str] = None, overwrite: bool = True) -> str:
        """
        Convert a single file to dst_ext and return the output file or folder path.
        """
        src_path = os.path.abspath(src_path)
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"Source file not found: {src_path}")

        src_p = Path(src_path)
        src_ext = src_p.suffix.lower().lstrip(".")
        dst_ext = dst_ext.lower().lstrip(".")

        if output_dir:
            out_dir = Path(output_dir).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = src_p.parent

        base_name = src_p.stem
        out_path = out_dir / f"{base_name}.{dst_ext}"

        if not overwrite and out_path.exists() and src_path != str(out_path):
            counter = 1
            while out_path.exists():
                out_path = out_dir / f"{base_name}_{counter}.{dst_ext}"
                counter += 1

        if src_ext == dst_ext:
            if src_path != str(out_path):
                shutil.copy2(src_path, out_path)
            return str(out_path)

        # Dispatch conversion logic
        # 1. Presentation inputs (PPT / PPTX)
        if src_ext in ("ppt", "pptx"):
            return self._convert_from_presentation(src_path, src_ext, dst_ext, out_path, out_dir, base_name)

        # 2. PDF input
        if src_ext == "pdf":
            return self._convert_from_pdf(src_path, dst_ext, out_path, out_dir, base_name)

        # 3. Word / RTF inputs
        if src_ext in ("doc", "docx", "rtf"):
            return self._convert_from_word(src_path, src_ext, dst_ext, out_path, out_dir, base_name)

        # 4. Text / Markdown / HTML inputs
        if src_ext in ("txt", "md", "html"):
            return self._convert_from_text_markup(src_path, src_ext, dst_ext, out_path, out_dir, base_name)

        # 5. Spreadsheet / Data inputs (XLS, XLSX, CSV, JSON)
        if src_ext in ("xls", "xlsx", "csv", "json"):
            return self._convert_from_spreadsheet(src_path, src_ext, dst_ext, out_path, out_dir, base_name)

        # 6. Image inputs
        if src_ext in ("png", "jpg", "jpeg", "webp", "bmp", "tiff"):
            return self._convert_from_image(src_path, src_ext, dst_ext, out_path, out_dir, base_name)

        raise ValueError(f"Unsupported conversion: .{src_ext} -> .{dst_ext}")

    # =========================================================================
    # PRESENTATION CONVERSIONS (PPT / PPTX)
    # =========================================================================
    def _convert_from_presentation(self, src_path: str, src_ext: str, dst_ext: str, out_path: Path, out_dir: Path, base_name: str) -> str:
        # PPT/PPTX -> PDF
        if dst_ext == "pdf":
            ppt = self._get_powerpoint()
            deck = None
            try:
                # ReadOnly=True, Untitled=False, WithWindow=False
                deck = ppt.Presentations.Open(src_path, True, False, False)
                # 32 = ppSaveAsPDF
                deck.SaveAs(str(out_path), 32)
                return str(out_path)
            finally:
                if deck is not None:
                    try:
                        deck.Close()
                    except Exception:
                        pass

        # PPT <-> PPTX
        if dst_ext in ("pptx", "ppt"):
            ppt = self._get_powerpoint()
            deck = None
            try:
                deck = ppt.Presentations.Open(src_path, True, False, False)
                # 24 = ppSaveAsOpenXMLPresentation (.pptx), 1 = ppSaveAsPresentation (.ppt)
                fmt_code = 24 if dst_ext == "pptx" else 1
                deck.SaveAs(str(out_path), fmt_code)
                return str(out_path)
            finally:
                if deck is not None:
                    try:
                        deck.Close()
                    except Exception:
                        pass

        # PPT/PPTX -> Images (PNG / JPG)
        if dst_ext in ("png", "jpg"):
            # Convert to temp PDF via PowerPoint COM, then render high-DPI images with PyMuPDF
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_pdf = os.path.join(tmpdir, f"{base_name}.pdf")
                self._convert_from_presentation(src_path, src_ext, "pdf", Path(tmp_pdf), Path(tmpdir), base_name)
                return self._render_pdf_to_images(tmp_pdf, dst_ext, out_dir, base_name)

        # PPT/PPTX -> TXT
        if dst_ext == "txt":
            if src_ext == "ppt":
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_pptx = os.path.join(tmpdir, f"{base_name}.pptx")
                    self._convert_from_presentation(src_path, "ppt", "pptx", Path(tmp_pptx), Path(tmpdir), base_name)
                    return self._extract_pptx_text(tmp_pptx, out_path)
            else:
                return self._extract_pptx_text(src_path, out_path)

        # PPT/PPTX -> DOCX
        if dst_ext == "docx":
            if src_ext == "ppt":
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_pptx = os.path.join(tmpdir, f"{base_name}.pptx")
                    self._convert_from_presentation(src_path, "ppt", "pptx", Path(tmp_pptx), Path(tmpdir), base_name)
                    return self._pptx_to_docx(tmp_pptx, out_path)
            else:
                return self._pptx_to_docx(src_path, out_path)

        raise ValueError(f"Unsupported presentation conversion: .{src_ext} -> .{dst_ext}")

    def _extract_pptx_text(self, pptx_path: str, out_path: Path) -> str:
        from pptx import Presentation
        prs = Presentation(pptx_path)
        lines = []
        for idx, slide in enumerate(prs.slides, 1):
            lines.append(f"--- Slide {idx} ---")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text:
                            lines.append(text)
                if shape.has_table:
                    for row in shape.table.rows:
                        row_text = " | ".join(cell.text.strip() for cell in row.cells)
                        if row_text.strip():
                            lines.append(row_text)
            lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)

    def _pptx_to_docx(self, pptx_path: str, out_path: Path) -> str:
        from pptx import Presentation
        from docx import Document
        prs = Presentation(pptx_path)
        doc = Document()
        doc.add_heading(out_path.stem, 0)
        for idx, slide in enumerate(prs.slides, 1):
            doc.add_heading(f"Slide {idx}", level=1)
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text:
                            doc.add_paragraph(text)
        doc.save(str(out_path))
        return str(out_path)

    # =========================================================================
    # PDF CONVERSIONS
    # =========================================================================
    def _convert_from_pdf(self, src_path: str, dst_ext: str, out_path: Path, out_dir: Path, base_name: str) -> str:
        import fitz  # PyMuPDF

        # PDF -> PPTX (Each PDF page becomes a widescreen/matched-aspect slide image)
        if dst_ext == "pptx":
            from pptx import Presentation
            from pptx.util import Inches
            doc = fitz.open(src_path)
            prs = Presentation()
            blank_layout = prs.slide_layouts[6]

            with tempfile.TemporaryDirectory() as tmpdir:
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    rect = page.rect
                    if page_num == 0 and rect.width > 0 and rect.height > 0:
                        # Match slide aspect ratio to PDF page
                        prs.slide_width = Inches(10)
                        prs.slide_height = Inches(10 * (rect.height / rect.width))

                    pix = page.get_pixmap(dpi=self.dpi)
                    img_path = os.path.join(tmpdir, f"page_{page_num + 1}.png")
                    pix.save(img_path)

                    slide = prs.slides.add_slide(blank_layout)
                    slide.shapes.add_picture(
                        img_path, 0, 0,
                        width=prs.slide_width,
                        height=prs.slide_height
                    )
            doc.close()
            prs.save(str(out_path))
            return str(out_path)

        # PDF -> DOCX
        if dst_ext == "docx":
            cv = None
            try:
                from pdf2docx import Converter
                cv = Converter(src_path)
                cv.convert(str(out_path))
                return str(out_path)
            except Exception:
                # Fallback to text extraction into DOCX if pdf2docx fails
                from docx import Document
                with fitz.open(src_path) as doc:
                    wdoc = Document()
                    for page_num in range(len(doc)):
                        text = doc.load_page(page_num).get_text("text")
                        if text.strip():
                            wdoc.add_paragraph(text)
                        if page_num < len(doc) - 1:
                            wdoc.add_page_break()
                    wdoc.save(str(out_path))
                return str(out_path)
            finally:
                if cv is not None:
                    try:
                        cv.close()
                    except Exception:
                        pass

        # PDF -> PNG / JPG
        if dst_ext in ("png", "jpg"):
            return self._render_pdf_to_images(src_path, dst_ext, out_dir, base_name)

        # PDF -> TXT
        if dst_ext == "txt":
            doc = fitz.open(src_path)
            pages_text = []
            for idx in range(len(doc)):
                pages_text.append(f"--- Page {idx + 1} ---\n" + doc.load_page(idx).get_text("text"))
            doc.close()
            out_path.write_text("\n\n".join(pages_text), encoding="utf-8")
            return str(out_path)

        # PDF -> HTML
        if dst_ext == "html":
            doc = fitz.open(src_path)
            html_parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>", base_name, "</title></head><body>"]
            for idx in range(len(doc)):
                html_parts.append(doc.load_page(idx).get_text("html"))
                html_parts.append("<hr/>")
            html_parts.append("</body></html>")
            doc.close()
            out_path.write_text("\n".join(html_parts), encoding="utf-8")
            return str(out_path)

        # PDF -> MD
        if dst_ext == "md":
            doc = fitz.open(src_path)
            md_parts = [f"# {base_name}\n"]
            for idx in range(len(doc)):
                md_parts.append(f"## Page {idx + 1}\n\n" + doc.load_page(idx).get_text("text"))
            doc.close()
            out_path.write_text("\n\n".join(md_parts), encoding="utf-8")
            return str(out_path)

        raise ValueError(f"Unsupported PDF conversion: .pdf -> .{dst_ext}")

    def _render_pdf_to_images(self, pdf_path: str, img_ext: str, out_dir: Path, base_name: str) -> str:
        import fitz
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        if num_pages == 1:
            out_file = out_dir / f"{base_name}.{img_ext}"
            pix = doc.load_page(0).get_pixmap(dpi=self.dpi)
            pix.save(str(out_file))
            doc.close()
            return str(out_file)
        else:
            slides_dir = out_dir / f"{base_name}_{img_ext}_pages"
            slides_dir.mkdir(parents=True, exist_ok=True)
            for idx in range(num_pages):
                pix = doc.load_page(idx).get_pixmap(dpi=self.dpi)
                img_file = slides_dir / f"{base_name}_page_{idx + 1:03d}.{img_ext}"
                pix.save(str(img_file))
            doc.close()
            return str(slides_dir)

    # =========================================================================
    # WORD CONVERSIONS (DOC / DOCX / RTF)
    # =========================================================================
    def _convert_from_word(self, src_path: str, src_ext: str, dst_ext: str, out_path: Path, out_dir: Path, base_name: str) -> str:
        # Word -> PPTX
        if dst_ext == "pptx":
            if src_ext != "docx":
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_docx = os.path.join(tmpdir, f"{base_name}.docx")
                    self._word_com_save(src_path, tmp_docx, 16)
                    return self._docx_to_pptx(tmp_docx, out_path)
            return self._docx_to_pptx(src_path, out_path)

        # Word -> MD
        if dst_ext == "md":
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_txt = Path(tmpdir) / f"{base_name}.txt"
                self._convert_from_word(src_path, src_ext, "txt", tmp_txt, Path(tmpdir), base_name)
                content = tmp_txt.read_text(encoding="utf-8", errors="replace")
                out_path.write_text(f"# {base_name}\n\n{content}", encoding="utf-8")
                return str(out_path)

        # Word COM FileFormat constants:
        # 17 = wdFormatPDF, 16 = wdFormatDocumentDefault (.docx), 0 = wdFormatDocument97 (.doc),
        # 7 = wdFormatUnicodeText (.txt), 8 = wdFormatHTML (.html), 6 = wdFormatRTF (.rtf)
        format_map = {
            "pdf": 17,
            "docx": 16,
            "doc": 0,
            "txt": 7,
            "html": 8,
            "rtf": 6,
        }
        if dst_ext in format_map:
            return self._word_com_save(src_path, str(out_path), format_map[dst_ext])

        raise ValueError(f"Unsupported Word conversion: .{src_ext} -> .{dst_ext}")

    def _word_com_save(self, src_path: str, dst_path: str, file_format: int) -> str:
        word = self._get_word()
        doc = None
        try:
            doc = word.Documents.Open(src_path, ReadOnly=True, Visible=False)
            doc.SaveAs2(dst_path, FileFormat=file_format)
            return dst_path
        finally:
            if doc is not None:
                try:
                    doc.Close(0)  # wdDoNotSaveChanges
                except Exception:
                    pass

    def _docx_to_pptx(self, docx_path: str, out_path: Path) -> str:
        from docx import Document
        from pptx import Presentation
        wdoc = Document(docx_path)
        prs = Presentation()
        layout = prs.slide_layouts[1]  # Title and Content

        paragraphs = [p.text.strip() for p in wdoc.paragraphs if p.text.strip()]
        if not paragraphs:
            paragraphs = ["Empty Document"]

        # Group every 6 paragraphs onto a slide
        chunk_size = 6
        for i in range(0, len(paragraphs), chunk_size):
            chunk = paragraphs[i:i + chunk_size]
            slide = prs.slides.add_slide(layout)
            slide.shapes.title.text = chunk[0][:80]
            body = slide.placeholders[1].text_frame
            body.clear()
            for line in chunk[1:] or [chunk[0]]:
                p = body.add_paragraph()
                p.text = line

        prs.save(str(out_path))
        return str(out_path)

    # =========================================================================
    # TEXT / MARKDOWN / HTML CONVERSIONS
    # =========================================================================
    def _convert_from_text_markup(self, src_path: str, src_ext: str, dst_ext: str, out_path: Path, out_dir: Path, base_name: str) -> str:
        raw_text = Path(src_path).read_text(encoding="utf-8", errors="replace")

        # To TXT
        if dst_ext == "txt":
            if src_ext == "html":
                import re
                clean = re.sub(r"<[^>]+>", "", raw_text)
                out_path.write_text(clean.strip(), encoding="utf-8")
            else:
                out_path.write_text(raw_text, encoding="utf-8")
            return str(out_path)

        # To MD
        if dst_ext == "md":
            if src_ext == "html":
                import re
                clean = re.sub(r"<[^>]+>", "", raw_text)
                out_path.write_text(f"# {base_name}\n\n{clean.strip()}", encoding="utf-8")
            else:
                out_path.write_text(raw_text, encoding="utf-8")
            return str(out_path)

        # To HTML
        if dst_ext == "html":
            if src_ext == "md":
                import markdown
                body = markdown.markdown(raw_text, extensions=["tables", "fenced_code"])
            else:
                import html as html_lib
                body = "<pre style='font-family: sans-serif; white-space: pre-wrap;'>" + html_lib.escape(raw_text) + "</pre>"
            full_html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{base_name}</title>"
                "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:850px;margin:40px auto;line-height:1.6;padding:0 20px;}"
                "table{border-collapse:collapse;width:100%;}th,td{border:1px solid #ccc;padding:8px;}</style>"
                f"</head><body>{body}</body></html>"
            )
            out_path.write_text(full_html, encoding="utf-8")
            return str(out_path)

        # To DOCX or PDF (convert to HTML first if MD/HTML, then use Word COM or python-docx)
        if dst_ext in ("docx", "pdf"):
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_html = Path(tmpdir) / f"{base_name}.html"
                if src_ext == "html":
                    tmp_html.write_text(raw_text, encoding="utf-8")
                else:
                    self._convert_from_text_markup(src_path, src_ext, "html", tmp_html, Path(tmpdir), base_name)
                fmt = 17 if dst_ext == "pdf" else 16
                return self._word_com_save(str(tmp_html), str(out_path), fmt)

        # To PPTX
        if dst_ext == "pptx":
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_docx = Path(tmpdir) / f"{base_name}.docx"
                self._convert_from_text_markup(src_path, src_ext, "docx", tmp_docx, Path(tmpdir), base_name)
                return self._docx_to_pptx(str(tmp_docx), out_path)

        raise ValueError(f"Unsupported text/markup conversion: .{src_ext} -> .{dst_ext}")

    # =========================================================================
    # SPREADSHEET / DATA CONVERSIONS (XLS / XLSX / CSV / JSON)
    # =========================================================================
    def _convert_from_spreadsheet(self, src_path: str, src_ext: str, dst_ext: str, out_path: Path, out_dir: Path, base_name: str) -> str:
        # Excel COM direct conversions for XLS / XLSX -> PDF / XLSX / XLS / HTML
        if src_ext in ("xls", "xlsx") and dst_ext in ("pdf", "xlsx", "xls", "html"):
            excel = self._get_excel()
            wb = None
            try:
                wb = excel.Workbooks.Open(src_path, ReadOnly=True)
                if dst_ext == "pdf":
                    # 0 = xlTypePDF
                    wb.ExportAsFixedFormat(0, str(out_path))
                elif dst_ext == "xlsx":
                    wb.SaveAs(str(out_path), FileFormat=51)  # xlOpenXMLWorkbook
                elif dst_ext == "xls":
                    wb.SaveAs(str(out_path), FileFormat=56)  # xlExcel8
                elif dst_ext == "html":
                    wb.SaveAs(str(out_path), FileFormat=44)  # xlHtml
                return str(out_path)
            finally:
                if wb is not None:
                    try:
                        wb.Close(False)
                    except Exception:
                        pass

        # Load tabular rows as list of lists
        rows = self._read_tabular_data(src_path, src_ext)

        if dst_ext == "csv":
            with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
            return str(out_path)

        if dst_ext == "json":
            if len(rows) > 1:
                headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
                records = [dict(zip(headers, r)) for r in rows[1:]]
            else:
                records = rows
            out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            return str(out_path)

        if dst_ext == "xlsx":
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = base_name[:31]
            for r in rows:
                ws.append([str(c) if c is not None else "" for c in r])
            wb.save(str(out_path))
            return str(out_path)

        if dst_ext == "txt":
            lines = ["\t".join(str(c) if c is not None else "" for c in r) for r in rows]
            out_path.write_text("\n".join(lines), encoding="utf-8")
            return str(out_path)

        if dst_ext == "md":
            if not rows:
                out_path.write_text("", encoding="utf-8")
                return str(out_path)
            headers = [str(c) if c is not None else "" for c in rows[0]]
            md_lines = [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join("---" for _ in headers) + " |"
            ]
            for r in rows[1:]:
                vals = [str(c).replace("\n", " ") if c is not None else "" for c in r]
                md_lines.append("| " + " | ".join(vals) + " |")
            out_path.write_text("\n".join(md_lines), encoding="utf-8")
            return str(out_path)

        if dst_ext in ("html", "pdf"):
            import html as html_lib
            table_rows = []
            for idx, r in enumerate(rows):
                tag = "th" if idx == 0 else "td"
                cells = "".join(f"<{tag}>{html_lib.escape(str(c) if c is not None else '')}</{tag}>" for c in r)
                table_rows.append(f"<tr>{cells}</tr>")
            html_str = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{base_name}</title>"
                "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;}"
                "table{border-collapse:collapse;width:100%;font-size:12px;}"
                "th,td{border:1px solid #bbb;padding:6px 10px;text-align:left;}"
                "th{background:#f1f5f9;}</style></head><body>"
                f"<h2>{html_lib.escape(base_name)}</h2>"
                f"<table>{''.join(table_rows)}</table></body></html>"
            )
            if dst_ext == "html":
                out_path.write_text(html_str, encoding="utf-8")
                return str(out_path)
            else:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_xlsx = Path(tmpdir) / f"{base_name}.xlsx"
                    self._convert_from_spreadsheet(src_path, src_ext, "xlsx", tmp_xlsx, Path(tmpdir), base_name)
                    return self._convert_from_spreadsheet(str(tmp_xlsx), "xlsx", "pdf", out_path, out_dir, base_name)

        raise ValueError(f"Unsupported spreadsheet conversion: .{src_ext} -> .{dst_ext}")

    def _read_tabular_data(self, src_path: str, src_ext: str) -> List[List]:
        if src_ext == "csv":
            with open(src_path, "r", encoding="utf-8-sig", errors="replace") as f:
                return list(csv.reader(f))

        if src_ext == "json":
            data = json.loads(Path(src_path).read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list) and data:
                if isinstance(data[0], dict):
                    headers = list(data[0].keys())
                    rows = [headers]
                    for item in data:
                        rows.append([item.get(k, "") for k in headers])
                    return rows
                elif isinstance(data[0], list):
                    return data
            return [[str(data)]]

        if src_ext in ("xls", "xlsx"):
            if src_ext == "xls":
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_xlsx = Path(tmpdir) / "temp.xlsx"
                    self._convert_from_spreadsheet(src_path, "xls", "xlsx", tmp_xlsx, Path(tmpdir), "temp")
                    return self._read_tabular_data(str(tmp_xlsx), "xlsx")
            import openpyxl
            wb = openpyxl.load_workbook(src_path, data_only=True)
            ws = wb.active
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
            wb.close()
            return rows

        return []

    # =========================================================================
    # IMAGE CONVERSIONS (PNG / JPG / JPEG / WEBP / BMP / TIFF)
    # =========================================================================
    def _convert_from_image(self, src_path: str, src_ext: str, dst_ext: str, out_path: Path, out_dir: Path, base_name: str) -> str:
        from PIL import Image

        # Image -> Image or PDF
        if dst_ext in ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "pdf"):
            with Image.open(src_path) as img:
                if dst_ext in ("jpg", "jpeg", "pdf") and img.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                elif dst_ext == "pdf" and img.mode != "RGB":
                    img = img.convert("RGB")

                save_fmt = "JPEG" if dst_ext in ("jpg", "jpeg") else dst_ext.upper()
                img.save(str(out_path), save_fmt, quality=95)
            return str(out_path)

        # Image -> PPTX (Slide containing the image fitted cleanly)
        if dst_ext == "pptx":
            from pptx import Presentation
            from pptx.util import Inches
            with Image.open(src_path) as img:
                w, h = img.size
            prs = Presentation()
            prs.slide_width = Inches(10)
            prs.slide_height = Inches(10 * (h / w)) if w > 0 else Inches(5.625)
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.shapes.add_picture(src_path, 0, 0, width=prs.slide_width, height=prs.slide_height)
            prs.save(str(out_path))
            return str(out_path)

        # Image -> DOCX
        if dst_ext == "docx":
            from docx import Document
            from docx.shared import Inches as DocxInches
            doc = Document()
            doc.add_Picture(src_path, width=DocxInches(6.0)) if hasattr(doc, "add_Picture") else doc.add_picture(src_path, width=DocxInches(6.0))
            doc.save(str(out_path))
            return str(out_path)

        raise ValueError(f"Unsupported image conversion: .{src_ext} -> .{dst_ext}")

    # =========================================================================
    # MULTI-FILE MERGE UTILITIES (Multiple Images / PDFs -> Single PDF or PPTX)
    # =========================================================================
    def merge_to_single_pdf(self, file_paths: List[str], out_pdf_path: str) -> str:
        """
        Combines multiple input files (PPTX, PPT, DOCX, Images, PDFs, etc.) into a single merged PDF.
        """
        import fitz
        merged_doc = fitz.open()
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, fpath in enumerate(file_paths):
                ext = Path(fpath).suffix.lower().lstrip(".")
                if ext == "pdf":
                    pdf_file = fpath
                else:
                    tmp_out = Path(tmpdir) / f"part_{idx:04d}.pdf"
                    pdf_file = self.convert_file(fpath, "pdf", output_dir=tmpdir, overwrite=True)
                    if pdf_file != str(tmp_out) and os.path.exists(pdf_file):
                        os.replace(pdf_file, str(tmp_out))
                        pdf_file = str(tmp_out)

                with fitz.open(pdf_file) as part_doc:
                    merged_doc.insert_pdf(part_doc)

        merged_doc.save(out_pdf_path)
        merged_doc.close()
        return out_pdf_path
