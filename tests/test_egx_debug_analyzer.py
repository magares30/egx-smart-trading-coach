"""Tests for EGX debug HTML analyzer."""

from pathlib import Path

import pytest

from core.egx_debug_analyzer import EgxDebugHtmlAnalyzer

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>EGX Market Prices</title>
  <link rel="stylesheet" href="/css/site.css">
  <script src="/js/jquery.min.js"></script>
  <script src="/api/MarketDataService.ashx"></script>
</head>
<body>
  <iframe src="/en/MarketFrame.aspx"></iframe>
  <form action="/en/SearchHandler.aspx" method="get"></form>
  <p>See https://egx.com.eg/en/GetPricesJson.aspx for data.</p>
</body>
</html>
"""


@pytest.fixture
def analyzer() -> EgxDebugHtmlAnalyzer:
    return EgxDebugHtmlAnalyzer()


def test_analyze_extracts_title_and_urls(
    analyzer: EgxDebugHtmlAnalyzer, tmp_path: Path
) -> None:
    html_path = tmp_path / "debug_stocks.html"
    html_path.write_text(SAMPLE_HTML, encoding="utf-8")

    result = analyzer.analyze(html_path)

    assert result.title == "EGX Market Prices"
    assert any("jquery.min.js" in url for url in result.script_src_urls)
    assert any("MarketDataService.ashx" in url for url in result.script_src_urls)
    assert any("site.css" in url for url in result.link_href_urls)
    assert any("MarketFrame.aspx" in url for url in result.iframe_src_urls)
    assert any("SearchHandler.aspx" in url for url in result.form_action_urls)
    assert any("GetPricesJson.aspx" in url for url in result.interesting_urls)


def test_analyze_missing_file_raises(analyzer: EgxDebugHtmlAnalyzer, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        analyzer.analyze(tmp_path / "missing.html")


def test_analyze_finds_no_interesting_urls_when_absent(
    analyzer: EgxDebugHtmlAnalyzer, tmp_path: Path
) -> None:
    html_path = tmp_path / "plain.html"
    html_path.write_text(
        "<html><head><title>Plain</title></head><body><p>Hello</p></body></html>",
        encoding="utf-8",
    )

    result = analyzer.analyze(html_path)

    assert result.title == "Plain"
    assert result.interesting_urls == []
