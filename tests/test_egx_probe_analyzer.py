"""Tests for EGX probe body analyzer."""

from pathlib import Path

import pytest

from core.egx_probe_analyzer import EgxProbeBodyAnalyzer

SAMPLE_PROBE_HTML = """
<!DOCTYPE html>
<html>
<head><title>EGX MarketFrame</title></head>
<body>
<script>
window.marketConfig = { type: "stocks", lang: "en" };
window["bobcmnSettings"] = true;
window[frameName] = {};
var PricesPayload = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
var seed = 12345678901234;
var endpoint = "https://egx.com.eg/en/GetPricesJson.aspx?type=1&market=EGX&lang=en&date=20260701";
fetch("https://egx.com.eg/api/MarketDataService.ashx?market=main");
</script>
<p>Symbol Last Volume data from MarketFrame and bobcmn module.</p>
</body>
</html>
"""


@pytest.fixture
def analyzer() -> EgxProbeBodyAnalyzer:
    return EgxProbeBodyAnalyzer()


def test_analyze_extracts_window_variables_and_assignments(
    analyzer: EgxProbeBodyAnalyzer, tmp_path: Path
) -> None:
    probe_path = tmp_path / "probe_MarketFrame_20260701_120000.txt"
    probe_path.write_text(SAMPLE_PROBE_HTML, encoding="utf-8")

    result = analyzer.analyze(probe_path)

    assert "marketConfig" in result.window_variable_names
    assert "bobcmnSettings" in result.window_variable_names
    assert any("PricesPayload" in item for item in result.large_js_assignments)
    assert any("12345678901234" in item for item in result.large_js_assignments)


def test_analyze_extracts_script_urls_and_query_parameters(
    analyzer: EgxProbeBodyAnalyzer, tmp_path: Path
) -> None:
    probe_path = tmp_path / "probe_GetPricesJson_20260701_120000.txt"
    probe_path.write_text(SAMPLE_PROBE_HTML, encoding="utf-8")

    result = analyzer.analyze(probe_path)

    assert any("GetPricesJson.aspx" in url for url in result.script_urls)
    assert any("MarketDataService.ashx" in url for url in result.script_urls)
    assert "type=1" in result.query_parameters
    assert "market=EGX" in result.query_parameters
    assert "lang=en" in result.query_parameters
    assert "date=20260701" in result.query_parameters


def test_analyze_finds_keyword_occurrences(
    analyzer: EgxProbeBodyAnalyzer, tmp_path: Path
) -> None:
    probe_path = tmp_path / "probe_MarketDataService_20260701_120000.txt"
    probe_path.write_text(SAMPLE_PROBE_HTML, encoding="utf-8")

    result = analyzer.analyze(probe_path)

    joined = "\n".join(result.keyword_occurrences)
    for keyword in (
        "GetPricesJson",
        "MarketDataService",
        "MarketFrame",
        "bobcmn",
        "Prices",
        "Symbol",
        "Last",
        "Volume",
    ):
        assert keyword in joined


def test_analyze_missing_file_raises(analyzer: EgxProbeBodyAnalyzer, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        analyzer.analyze(tmp_path / "missing_probe.txt")


def test_analyze_plain_text_probe_body(
    analyzer: EgxProbeBodyAnalyzer, tmp_path: Path
) -> None:
    probe_path = tmp_path / "probe_plain.txt"
    probe_path.write_text(
        'window.dataRef = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"; '
        "GetPricesJson?type=2&market=main&lang=ar",
        encoding="utf-8",
    )

    result = analyzer.analyze(probe_path)

    assert "dataRef" in result.window_variable_names
    assert result.query_parameters == ["lang=ar", "market=main", "type=2"]
    assert any("GetPricesJson" in item for item in result.keyword_occurrences)
