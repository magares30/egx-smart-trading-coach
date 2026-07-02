"""Analyze saved EGX debug HTML files for embedded URLs and endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

INTERESTING_URL_KEYWORDS = (
    "ajax",
    "api",
    "handler",
    "service",
    "Get",
    "Market",
    "Prices",
    "Data",
    "json",
    "asmx",
    "ashx",
)

_URL_IN_TEXT_PATTERN = re.compile(r"""https?://[^\s"'<>]+""")


class EgxDebugHtmlAnalysis(BaseModel):
    html_path: Path
    title: str | None = None
    script_src_urls: list[str] = Field(default_factory=list)
    link_href_urls: list[str] = Field(default_factory=list)
    iframe_src_urls: list[str] = Field(default_factory=list)
    form_action_urls: list[str] = Field(default_factory=list)
    interesting_urls: list[str] = Field(default_factory=list)


class EgxDebugHtmlAnalyzer:
    """Inspect local EGX debug HTML without making network requests."""

    def _dedupe_sorted(self, urls: list[str]) -> list[str]:
        return sorted(dict.fromkeys(url for url in urls if url))

    def _is_interesting_url(self, url: str) -> bool:
        return any(keyword.lower() in url.lower() for keyword in INTERESTING_URL_KEYWORDS)

    def _resolve_url(self, raw_url: str, base_url: str) -> str:
        cleaned = raw_url.strip()
        if not cleaned or cleaned.startswith("#"):
            return ""
        if cleaned.lower().startswith(("javascript:", "mailto:", "tel:")):
            return ""
        return urljoin(base_url, cleaned)

    def _extract_urls_from_text(self, text: str) -> list[str]:
        return self._dedupe_sorted(_URL_IN_TEXT_PATTERN.findall(text))

    def analyze(self, html_path: Path) -> EgxDebugHtmlAnalysis:
        """Analyze a local debug HTML file and extract embedded URLs."""
        if not html_path.exists():
            raise FileNotFoundError(f"Debug HTML file not found: {html_path}")

        html = html_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        base_url = "https://egx.com.eg/en/"

        title = soup.title.string.strip() if soup.title and soup.title.string else None

        script_src_urls = self._dedupe_sorted(
            self._resolve_url(tag.get("src", ""), base_url)
            for tag in soup.find_all("script", src=True)
            if self._resolve_url(tag.get("src", ""), base_url)
        )
        link_href_urls = self._dedupe_sorted(
            self._resolve_url(tag.get("href", ""), base_url)
            for tag in soup.find_all("link", href=True)
            if self._resolve_url(tag.get("href", ""), base_url)
        )
        iframe_src_urls = self._dedupe_sorted(
            self._resolve_url(tag.get("src", ""), base_url)
            for tag in soup.find_all("iframe", src=True)
            if self._resolve_url(tag.get("src", ""), base_url)
        )
        form_action_urls = self._dedupe_sorted(
            self._resolve_url(tag.get("action", ""), base_url)
            for tag in soup.find_all("form")
            if self._resolve_url(tag.get("action", ""), base_url)
        )

        all_urls = self._dedupe_sorted(
            script_src_urls
            + link_href_urls
            + iframe_src_urls
            + form_action_urls
            + self._extract_urls_from_text(html)
        )
        interesting_urls = [url for url in all_urls if self._is_interesting_url(url)]

        return EgxDebugHtmlAnalysis(
            html_path=html_path,
            title=title,
            script_src_urls=script_src_urls,
            link_href_urls=link_href_urls,
            iframe_src_urls=iframe_src_urls,
            form_action_urls=form_action_urls,
            interesting_urls=interesting_urls,
        )
