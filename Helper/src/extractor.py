from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, List, Optional
import numpy as np
from PIL import Image


@dataclass
class QuestionPayload:
    """Compact structured payload representing an extracted question and MCQ options."""

    question_stem: str
    options: List[str] = field(default_factory=list)
    is_mcq: bool = False
    question_type: str = "general"  # "mcq" | "fill_in_blank" | "open_question" | "vision_direct"
    raw_text: str = ""
    ocr_engine_used: str = "vision"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        lines = [f"QUESTION:\n{self.question_stem.strip()}"]
        if self.options:
            opts_formatted = "\n".join(f"  {opt}" for opt in self.options)
            lines.append(f"\nOPTIONS:\n{opts_formatted}")
        return "\n".join(lines)


# Regex patterns for Question & MCQ parsing
OPTION_LINE_RE = re.compile(
    r"^\s*(?:"
    r"[\(\[]?([A-Fa-f]|[1-6])[\)\]\.\:\-]\s+"  # A) A. (A) [A] 1) 1.
    r"|([•\-\*\u2022\u25E6\u25AA])\s+"          # Bulleted lists
    r")(.+)$"
)

INLINE_OPTIONS_RE = re.compile(
    r"(?:^|\s+)(?:[\(\[]?[A-Da-d][\)\]\.\:]\s+[^\n\r\(\[]+){2,}"
)

BLANK_PATTERN_RE = re.compile(r"_{2,}|\[\s*\.\.\.\s*\]|\(\s*\.\.\.\s*\)|\bblank\b", re.IGNORECASE)

QUESTION_STARTER_RE = re.compile(
    r"^(?:q(?:uestion)?\s*\d*[\.\:\)]?\s*)?"
    r"(?:what|which|who|whom|whose|where|when|why|how|select|choose|identify|"
    r"find|calculate|determine|complete|fill|is|are|does|do|can|will|should|if|given)\b",
    re.IGNORECASE,
)


class QuestionExtractor:
    """
    Handles OCR text extraction (RapidOCR / Windows Media OCR / Tesseract / Vision)
    and parses multi-line question stems + MCQ options into a QuestionPayload.
    """

    def __init__(self, ocr_cfg: Dict[str, Any]) -> None:
        self.cfg = ocr_cfg
        self.preferred_engine = str(ocr_cfg.get("engine", "auto")).lower()
        self.min_len = int(ocr_cfg.get("min_question_length", 12))
        self.require_pattern = bool(ocr_cfg.get("require_question_pattern", False))

        self._rapid_ocr = None
        self._tesseract_ready = False
        self._winocr_ready = False
        self._active_engine = "vision"
        self._init_ocr_backend()

    def _init_ocr_backend(self) -> None:
        if self.preferred_engine == "vision":
            self._active_engine = "vision"
            return

        # 1. Try RapidOCR (ONNX Runtime, fast & offline)
        if self.preferred_engine in ("auto", "rapidocr"):
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                self._rapid_ocr = RapidOCR()
                self._active_engine = "rapidocr"
                return
            except Exception:
                pass

        # 2. Try winocr (Windows Media OCR native WinRT binding)
        if self.preferred_engine in ("auto", "winocr"):
            try:
                import winocr  # type: ignore  # noqa: F401

                self._winocr_ready = True
                self._active_engine = "winocr"
                return
            except Exception:
                pass

        # 3. Try pytesseract (Tesseract OCR)
        if self.preferred_engine in ("auto", "tesseract"):
            try:
                import pytesseract  # type: ignore

                tess_cmd = self.cfg.get("tesseract_cmd", "tesseract")
                if tess_cmd:
                    pytesseract.pytesseract.tesseract_cmd = tess_cmd
                pytesseract.get_tesseract_version()
                self._tesseract_ready = True
                self._active_engine = "tesseract"
                return
            except Exception:
                pass

        # 4. Fallback to direct multimodal Vision LLM parsing
        self._active_engine = "vision"

    @property
    def active_engine(self) -> str:
        return self._active_engine

    def run_ocr(self, image: Image.Image) -> str:
        """Extract raw text from image using the configured local OCR engine."""
        if self._active_engine == "rapidocr" and self._rapid_ocr is not None:
            try:
                arr = np.asarray(image)
                result, _ = self._rapid_ocr(arr)
                if not result:
                    return ""
                return "\n".join(line[1] for line in result if len(line) > 1 and line[1])
            except Exception as exc:
                print(f"[OCR] RapidOCR error: {exc}")
                return ""

        if self._active_engine == "winocr" and self._winocr_ready:
            try:
                import winocr  # type: ignore

                res = winocr.recognize_pil_sync(image, "en")
                if isinstance(res, dict) and "text" in res:
                    return str(res["text"])
                return str(getattr(res, "text", ""))
            except Exception as exc:
                print(f"[OCR] WinOCR error: {exc}")
                return ""

        if self._active_engine == "tesseract" and self._tesseract_ready:
            try:
                import pytesseract  # type: ignore

                return str(pytesseract.image_to_string(image))
            except Exception as exc:
                print(f"[OCR] Tesseract error: {exc}")
                return ""

        return ""

    def extract_payload(self, image: Image.Image, force: bool = False) -> Optional[QuestionPayload]:
        """
        Run OCR (if local engine active) and parse question & MCQ options.
        If using 'vision' engine, returns a vision_direct payload so the multimodal LLM
        parses the screenshot directly.
        """
        if self._active_engine == "vision":
            return QuestionPayload(
                question_stem="[Multimodal Vision Frame Analysis]",
                options=[],
                is_mcq=False,
                question_type="vision_direct",
                raw_text="",
                ocr_engine_used="vision",
            )

        raw_text = self.run_ocr(image)
        if not raw_text or len(raw_text.strip()) < self.min_len:
            if force:
                # Fall back to vision frame analysis if user forced capture and OCR saw too little
                return QuestionPayload(
                    question_stem="[Multimodal Vision Frame Analysis]",
                    options=[],
                    is_mcq=False,
                    question_type="vision_direct",
                    raw_text=raw_text or "",
                    ocr_engine_used="vision",
                )
            return None

        return self.parse_text(raw_text, force=force, engine_name=self._active_engine)

    def parse_text(
        self, raw_text: str, force: bool = False, engine_name: str = "ocr"
    ) -> Optional[QuestionPayload]:
        """
        Parse raw OCR text to identify:
        - Multi-line question stems ending with '?', ':', or fill-in-the-blanks
        - Multiple-choice options (A/B/C/D, 1/2/3/4, or bullets)
        """
        cleaned_lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in raw_text.splitlines()
            if len(line.strip()) > 1
        ]
        if not cleaned_lines:
            return None

        options: List[str] = []
        stem_candidates: List[str] = []

        for line in cleaned_lines:
            opt_match = OPTION_LINE_RE.match(line)
            if opt_match:
                marker = opt_match.group(1) or opt_match.group(2) or "-"
                content = opt_match.group(3).strip()
                if content:
                    options.append(f"{marker.upper()}) {content}" if marker.isalnum() else f"• {content}")
                continue

            # Check if a single line contains multiple inline options like "A) Foo  B) Bar  C) Baz"
            if INLINE_OPTIONS_RE.search(line):
                parts = re.split(r"\s+(?=[\(\[]?[A-Da-d][\)\]\.\:]\s+)", line)
                for part in parts:
                    m = OPTION_LINE_RE.match(part.strip())
                    if m:
                        marker = m.group(1) or "-"
                        options.append(f"{marker.upper()}) {m.group(3).strip()}")
                    else:
                        stem_candidates.append(part.strip())
                continue

            stem_candidates.append(line)

        # Group stem lines to locate question stems ending with '?', ':', or containing blanks
        full_stem_text = "\n".join(stem_candidates).strip()
        selected_stem = ""
        question_type = "general"

        # Find multi-line blocks ending in '?' or ':'
        blocks = [b.strip() for b in re.split(r"\n{2,}", full_stem_text) if b.strip()]
        if not blocks:
            blocks = [full_stem_text] if full_stem_text else []

        matched_blocks: List[str] = []
        for blk in stem_candidates:
            if (
                blk.endswith("?")
                or blk.endswith(":")
                or BLANK_PATTERN_RE.search(blk)
                or QUESTION_STARTER_RE.search(blk)
            ):
                matched_blocks.append(blk)

        if matched_blocks:
            selected_stem = "\n".join(stem_candidates)
        elif options or not self.require_pattern or force:
            selected_stem = "\n".join(stem_candidates) if stem_candidates else raw_text.strip()
        else:
            return None

        if len(selected_stem.strip()) < self.min_len and not options and not force:
            return None

        is_mcq = len(options) >= 2
        if is_mcq:
            question_type = "mcq"
        elif BLANK_PATTERN_RE.search(selected_stem):
            question_type = "fill_in_blank"
        elif "?" in selected_stem or ":" in selected_stem:
            question_type = "open_question"

        return QuestionPayload(
            question_stem=selected_stem.strip(),
            options=options[:24],
            is_mcq=is_mcq,
            question_type=question_type,
            raw_text=raw_text.strip(),
            ocr_engine_used=engine_name,
        )
