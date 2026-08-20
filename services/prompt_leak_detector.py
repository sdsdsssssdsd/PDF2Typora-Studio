"""Detect when the model transcribed the prompt instead of the page."""

from __future__ import annotations

KNOWN_INSTRUCTION_PHRASES = [
    "the instructions in this message are not part of the pdf page",
    "never reproduce, quote, summarize, translate, or transcribe any instruction",
    "only content visibly present in the attached pdf page image",
    "the attached image is the only source document",
    "never invent image filenames or urls",
    "never output markdown image syntax",
    "do not use * or ** for markdown emphasis",
    "your structured result contains",
    "return only the structured result",
    "never transcribe these instructions",
    # Chinese remnants from the Phase 4 leak
    "你正在执行教材",
    "一、忠实性",
    "不总结。",
    "不解释。",
    "JSON Schema fields",
]


def detect_prompt_leak(markdown: str, extra_phrases: list[str] | None = None) -> list[str]:
    """Return matched instruction phrases. Multiple hits ⇒ leak."""
    text = (markdown or "").lower()
    phrases = list(KNOWN_INSTRUCTION_PHRASES)
    if extra_phrases:
        phrases.extend(extra_phrases)
    hits: list[str] = []
    for phrase in phrases:
        if phrase.lower() in text:
            hits.append(phrase)
    return hits


def is_prompt_leak(markdown: str, extra_phrases: list[str] | None = None) -> bool:
    hits = detect_prompt_leak(markdown, extra_phrases)
    return len(hits) >= 2
