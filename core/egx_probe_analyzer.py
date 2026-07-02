"""Analyze saved EGX probe response files for embedded JS and endpoint hints."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

QUERY_PARAM_KEYS = ("type", "market", "lang", "date")
PROBE_KEYWORDS = (
    "GetPricesJson",
    "MarketDataService",
    "MarketFrame",
    "bobcmn",
    "Prices",
    "Symbol",
    "Last",
    "Volume",
)

LARGE_STRING_MIN_LEN = 80
LARGE_NUMERIC_MIN_DIGITS = 8
SNIPPET_RADIUS = 60
MAX_ASSIGNMENT_LEN = 200

_WINDOW_DOT_PATTERN = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)")
_WINDOW_BRACKET_QUOTED_PATTERN = re.compile(
    r"""\bwindow\[["']([^"']+)["']\]"""
)
_WINDOW_BRACKET_IDENT_PATTERN = re.compile(
    r"\bwindow\[([A-Za-z_$][\w$]*)\]"
)
_URL_PATTERN = re.compile(r"""https?://[^\s"'<>]+""")
_SCRIPT_BLOCK_PATTERN = re.compile(
    r"<script[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
_QUERY_PARAM_PATTERN = re.compile(
    r"\b(" + "|".join(QUERY_PARAM_KEYS) + r")=([^&\s\"'<>]+)",
    re.IGNORECASE,
)
_LARGE_STRING_ASSIGN_PATTERN = re.compile(
    rf"""([\w$.[\]"']+\s*=\s*["'][^"']{{{LARGE_STRING_MIN_LEN},}}["'])"""
)
_LARGE_NUMERIC_ASSIGN_PATTERN = re.compile(
    rf"""([\w$.]+\s*=\s*\d{{{LARGE_NUMERIC_MIN_DIGITS},}})"""
)


class EgxProbeBodyAnalysis(BaseModel):
    probe_path: Path
    window_variable_names: list[str] = Field(default_factory=list)
    large_js_assignments: list[str] = Field(default_factory=list)
    script_urls: list[str] = Field(default_factory=list)
    query_parameters: list[str] = Field(default_factory=list)
    keyword_occurrences: list[str] = Field(default_factory=list)


class EgxProbeBodyAnalyzer:
    """Inspect local EGX probe response files without making network requests."""

    def _dedupe_sorted(self, values: list[str]) -> list[str]:
        return sorted(dict.fromkeys(value for value in values if value))

    def _truncate(self, text: str, limit: int) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

    def _extract_script_blocks(self, text: str) -> list[str]:
        blocks = [
            block.strip()
            for block in _SCRIPT_BLOCK_PATTERN.findall(text)
            if block.strip()
        ]
        if blocks:
            return blocks
        return [text]

    def _extract_window_variable_names(self, text: str) -> list[str]:
        names: list[str] = []
        names.extend(_WINDOW_DOT_PATTERN.findall(text))
        names.extend(_WINDOW_BRACKET_QUOTED_PATTERN.findall(text))
        names.extend(_WINDOW_BRACKET_IDENT_PATTERN.findall(text))
        return self._dedupe_sorted(names)

    def _extract_large_js_assignments(self, text: str) -> list[str]:
        assignments: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue

            for pattern in (_LARGE_STRING_ASSIGN_PATTERN, _LARGE_NUMERIC_ASSIGN_PATTERN):
                match = pattern.search(stripped)
                if match:
                    assignments.append(self._truncate(match.group(1), MAX_ASSIGNMENT_LEN))
                    break

        return self._dedupe_sorted(assignments)

    def _extract_script_urls(self, text: str, script_blocks: list[str]) -> list[str]:
        urls = _URL_PATTERN.findall(text)
        for block in script_blocks:
            urls.extend(_URL_PATTERN.findall(block))
        return self._dedupe_sorted(urls)

    def _extract_query_parameters(self, text: str) -> list[str]:
        params: list[str] = []
        for key, value in _QUERY_PARAM_PATTERN.findall(text):
            params.append(f"{key.lower()}={value}")
        return self._dedupe_sorted(params)

    def _extract_keyword_occurrences(self, text: str) -> list[str]:
        occurrences: list[str] = []
        lower_text = text.lower()

        for keyword in PROBE_KEYWORDS:
            start = 0
            key_lower = keyword.lower()
            while True:
                index = lower_text.find(key_lower, start)
                if index < 0:
                    break

                snippet_start = max(0, index - SNIPPET_RADIUS)
                snippet_end = min(len(text), index + len(keyword) + SNIPPET_RADIUS)
                snippet = self._truncate(text[snippet_start:snippet_end].strip(), 160)
                occurrences.append(f"{keyword}: {snippet}")
                start = index + len(keyword)

        return self._dedupe_sorted(occurrences)

    def analyze(self, probe_path: Path) -> EgxProbeBodyAnalysis:
        """Analyze a local probe response file and extract embedded hints."""
        if not probe_path.exists():
            raise FileNotFoundError(f"Probe file not found: {probe_path}")

        text = probe_path.read_text(encoding="utf-8", errors="replace")
        script_blocks = self._extract_script_blocks(text)

        return EgxProbeBodyAnalysis(
            probe_path=probe_path,
            window_variable_names=self._extract_window_variable_names(text),
            large_js_assignments=self._extract_large_js_assignments(text),
            script_urls=self._extract_script_urls(text, script_blocks),
            query_parameters=self._extract_query_parameters(text),
            keyword_occurrences=self._extract_keyword_occurrences(text),
        )
