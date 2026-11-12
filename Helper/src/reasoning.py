import base64
from dataclasses import dataclass, field
import json
import re
import time
from typing import Any, Dict, List, Optional
import requests

from src.extractor import QuestionPayload


SYSTEM_PROMPT = """You are an ultra-concise question-answering assistant.
Analyze the provided screen text and/or screenshot to identify all visible questions, quiz prompts, or multiple-choice problems on the screen.

Respond ONLY in valid JSON with this exact schema:
{
  "has_question": true,
  "items": [
    {
      "number": "1",
      "answer": "B) Paris"
    }
  ]
}

Rules:
1. Include an entry in "items" for EVERY question visible on the screen, in order.
2. "number" must be the exact question number shown on screen (e.g., "1", "14", "3b"). If no number is shown, number them sequentially starting at "1".
3. If multiple-choice options (A, B, C, D / 1, 2, 3, 4) are present, start "answer" with the option letter/number AND the key answer text (e.g., "B) O(n log n)").
4. Keep each "answer" ultra-short (just the key answer, under 8 words). No explanations.
5. If no question is visible at all, set "has_question": false and "items": [].
"""


class GeminiError(RuntimeError):
    """Raised when every Gemini model fails; carries a short user-facing summary."""

    def __init__(self, summary: str, details: str) -> None:
        super().__init__(f"All Gemini models failed: {details}")
        self.summary = summary


@dataclass
class ReasoningResult:
    has_question: bool
    question_summary: str = ""
    answer: str = ""
    items: List[Dict[str, str]] = field(default_factory=list)
    rationale: str = ""
    latency_ms: int = 0
    provider: str = "gemini"
    error: Optional[str] = None

    def format_lines(self) -> List[str]:
        """Return minimal 'Q#: Answer' lines with zero extra fluff."""
        if self.error:
            return [f"Err: {self.answer or self.error[:40]}"]
        if not self.has_question:
            return []
        lines: List[str] = []
        for idx, item in enumerate(self.items, start=1):
            raw_num = str(item.get("number", idx)).strip()
            raw_ans = str(item.get("answer", "")).strip()
            if not raw_ans:
                continue
            clean_num = re.sub(r"^(?:question|q)\s*", "", raw_num, flags=re.IGNORECASE).strip(" .:#)")
            if not clean_num:
                clean_num = str(idx)
            lines.append(f"Q{clean_num}: {raw_ans}")
        if not lines and self.answer and self.answer != "—":
            lines.append(self.answer)
        return lines



class ReasoningEngine:
    """
    Low-latency REST client for Google Gemini Free Flash/Lite models
    (gemini-3.1-flash-lite, gemini-3.5-flash-lite, gemini-3.6-flash, gemini-3.5-flash)
    with automatic fast failover and zero-thinking-budget for sub-second responses.
    """

    def __init__(self, llm_cfg: Dict[str, Any]) -> None:
        self.cfg = llm_cfg
        self.session = requests.Session()
        self._last_good_model: Optional[str] = None
        self._cooldown_until: Dict[str, float] = {}
        self._no_thinking_cfg: set = set()

    @property
    def provider(self) -> str:
        return str(self.cfg.get("provider", "gemini")).lower()

    def is_configured(self) -> bool:
        if self.provider == "openai":
            key = str(self.cfg.get("openai_api_key", "")).strip()
            return bool(key and not key.startswith("your_"))
        key = str(self.cfg.get("gemini_api_key", "")).strip()
        return bool(key and not key.startswith("your_"))

    def _get_gemini_model_chain(self) -> List[str]:
        primary = str(self.cfg.get("gemini_model", "gemini-3.1-flash-lite")).strip()
        fallbacks = self.cfg.get(
            "gemini_fallback_models",
            [
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
            ],
        )
        chain: List[str] = []
        for m in [primary] + list(fallbacks):
            if m and m not in chain:
                chain.append(m)
        return chain

    def analyze(
        self,
        payload: QuestionPayload,
        png_bytes: Optional[bytes] = None,
    ) -> ReasoningResult:
        """Analyze extracted QuestionPayload (and/or screenshot bytes) and return question numbers + key answers."""
        start_t = time.perf_counter()

        if not self.is_configured():
            return ReasoningResult(
                has_question=False,
                question_summary="Setup",
                answer="Add API key in config.json",
                items=[],
                latency_ms=0,
                provider=self.provider,
                error="missing_api_key",
            )

        try:
            if self.provider == "openai":
                raw_resp, used_model = self._call_openai(payload, png_bytes)
            else:
                raw_resp, used_model = self._call_gemini_with_failover(payload, png_bytes)

            latency_ms = int((time.perf_counter() - start_t) * 1000)
            parsed = self._parse_json_response(raw_resp)
            has_q = bool(parsed.get("has_question", True))
            raw_items = parsed.get("items")

            items: List[Dict[str, str]] = []
            if isinstance(raw_items, list):
                for idx, entry in enumerate(raw_items, start=1):
                    if isinstance(entry, dict):
                        num = str(entry.get("number", entry.get("question_number", idx))).strip()
                        ans = str(entry.get("answer", "")).strip()
                        if ans and ans != "—":
                            items.append({"number": num or str(idx), "answer": ans})

            if not items and has_q:
                fallback_ans = str(parsed.get("answer", "")).strip()
                if fallback_ans and fallback_ans != "—":
                    items.append({"number": "1", "answer": fallback_ans})
                else:
                    has_q = False

            result = ReasoningResult(
                has_question=has_q and len(items) > 0,
                items=items,
                latency_ms=latency_ms,
                provider=used_model,
            )
            lines = result.format_lines()
            result.answer = "\n".join(lines) if lines else "—"
            result.question_summary = ", ".join(f"Q{it['number']}" for it in items) if items else ""
            return result
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start_t) * 1000)
            print(f"[Reasoning] {exc}")
            return ReasoningResult(
                has_question=False,
                question_summary="Error",
                answer=exc.summary if isinstance(exc, GeminiError) else "API error",
                items=[],
                rationale=str(exc)[:90],
                latency_ms=latency_ms,
                provider=self.provider,
                error=str(exc),
            )

    def _call_gemini_with_failover(
        self, payload: QuestionPayload, png_bytes: Optional[bytes]
    ) -> tuple[str, str]:
        api_key = str(self.cfg.get("gemini_api_key", "")).strip()
        timeout = float(self.cfg.get("timeout_seconds", 10.0))
        models = self._get_gemini_model_chain()

        parts = []
        if payload.question_type == "vision_direct" and png_bytes:
            parts.append(
                {
                    "text": (
                        f"{SYSTEM_PROMPT}\n\n"
                        "Inspect this screenshot, find all visible questions and their question numbers, "
                        "and return the JSON response."
                    )
                }
            )
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(png_bytes).decode("ascii"),
                    }
                }
            )
        else:
            user_text = f"{SYSTEM_PROMPT}\n\n{payload.to_prompt_text()}"
            parts.append({"text": user_text})
            if png_bytes:
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(png_bytes).decode("ascii"),
                        }
                    }
                )

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": float(self.cfg.get("temperature", 0.1)),
                "maxOutputTokens": int(self.cfg.get("max_tokens", 240)),
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }

        fallback_body = {
            "contents": body["contents"],
            "generationConfig": {
                k: v for k, v in body["generationConfig"].items() if k != "thinkingConfig"
            },
        }
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

        # Try the last model that worked first; skip models that are cooling down
        # after a quota (429) / not-found (404) error.
        now = time.monotonic()
        ordered = sorted(
            models,
            key=lambda m: (0 if m == self._last_good_model else 1),
        )
        ready = [m for m in ordered if self._cooldown_until.get(m, 0) <= now]
        if not ready:
            ready = ordered

        errors: List[str] = []
        for model in ready:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                use_body = fallback_body if model in self._no_thinking_cfg else body
                resp = self.session.post(url, headers=headers, json=use_body, timeout=timeout)
                if resp.status_code == 400 and use_body is body:
                    # Many models reject thinkingConfig with a generic "invalid argument".
                    self._no_thinking_cfg.add(model)
                    resp = self.session.post(url, headers=headers, json=fallback_body, timeout=timeout)

                if resp.status_code != 200:
                    msg = self._short_error(resp)
                    if resp.status_code == 429:
                        self._cooldown_until[model] = time.monotonic() + 60
                    elif resp.status_code == 404:
                        self._cooldown_until[model] = time.monotonic() + 3600
                    elif resp.status_code in (500, 503):
                        self._cooldown_until[model] = time.monotonic() + 15
                    errors.append(f"{model}: {resp.status_code} {msg}")
                    continue

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    errors.append(f"{model}: empty response")
                    continue
                content_parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(
                    p.get("text", "") for p in content_parts if not p.get("thought")
                )
                if text.strip():
                    self._last_good_model = model
                    return text, model
                errors.append(f"{model}: no text")
            except requests.exceptions.Timeout:
                errors.append(f"{model}: timeout")
            except Exception as exc:
                errors.append(f"{model}: {str(exc)[:80]}")

        codes = " ".join(errors)
        if "429" in codes and all(("429" in e or "503" in e or "404" in e) for e in errors):
            summary = "Rate limited, wait a minute"
        elif "503" in codes:
            summary = "Gemini busy, retry"
        elif "timeout" in codes:
            summary = "Timeout, retry"
        elif "401" in codes or "403" in codes:
            summary = "Invalid API key"
        else:
            summary = "API error"
        raise GeminiError(summary, "; ".join(errors))

    @staticmethod
    def _short_error(resp: "requests.Response") -> str:
        try:
            return str(resp.json().get("error", {}).get("message", ""))[:100]
        except Exception:
            return resp.text[:100]

    def _call_openai(
        self, payload: QuestionPayload, png_bytes: Optional[bytes]
    ) -> tuple[str, str]:
        api_key = str(self.cfg.get("openai_api_key", "")).strip()
        model = str(self.cfg.get("openai_model", "gpt-4o-mini")).strip()
        timeout = float(self.cfg.get("timeout_seconds", 12.0))

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        user_content: Any
        if png_bytes:
            b64_img = base64.b64encode(png_bytes).decode("ascii")
            prompt_text = (
                "Extract all visible questions and their question numbers from this screen capture."
                if payload.question_type == "vision_direct"
                else payload.to_prompt_text()
            )
            user_content = [
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_img}", "detail": "low"},
                },
            ]
        else:
            user_content = payload.to_prompt_text()

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": float(self.cfg.get("temperature", 0.1)),
            "max_tokens": int(self.cfg.get("max_tokens", 240)),
            "response_format": {"type": "json_object"},
        }

        resp = self.session.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], model

    @staticmethod
    def _parse_json_response(raw_text: str) -> Dict[str, Any]:
        cleaned = raw_text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return {
                "has_question": True,
                "items": [{"number": "1", "answer": cleaned[:120]}],
            }
