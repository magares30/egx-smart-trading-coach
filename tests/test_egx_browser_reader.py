"""Tests for EGX public browser stocks reader helpers."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from core.data_import import LIVE_SNAPSHOT_OUTPUT_COLUMNS
from core.egx_browser_reader import (
    DEFAULT_CHROME_CDP_URL,
    EGX_BROWSER_LAUNCH_ARGS,
    EGX_BROWSER_STOCKS_URL,
    EGX_BROWSER_STOCKS_URLS,
    HEADER_SEARCH_INPUT_ID,
    LOW_ROW_COUNT_THRESHOLD,
    LOW_ROW_COUNT_WARNING,
    PAGE_LOAD_TIMEOUT_MS,
    LAST_PRICE_CLOSE_WARNING,
    INVALID_OHLC_RANGE_WARNING,
    NO_EGX_PRICES_PAGE_ERROR,
    NO_STOCKS_TABLE_ERROR,
    RESET_FILTER_WARNING_PREFIX,
    VOLUME_MISSING_WARNING,
    EgxAttachedChromeStocksReader,
    EgxPublicBrowserStocksReader,
    ALREADY_ON_STOCKS_TRADING_DATA_MESSAGE,
    COMPANY_FILTER_UNAVAILABLE_WARNING,
    MARKET_SEGMENT_TAB_TEXT,
    MARKET_SEGMENT_FINGERPRINT_UNCHANGED_WARNING,
    MULTI_MARKET_SEGMENT_COLLECTED_WARNING,
    MULTI_SECTOR_REUSED_TABLE_CRITICAL_WARNING,
    NO_VISIBLE_FILTER_SUBMIT_WARNING,
    FILTER_DUPLICATE_FINGERPRINT_WARNING,
    SECTOR_DUPLICATE_FINGERPRINT_WARNING,
    SEVERAL_FILTER_FINGERPRINTS_UNCHANGED_WARNING,
    SECTOR_FINGERPRINT_UNCHANGED_WARNING,
    SECTOR_TAB_TEXT,
    _activate_filter_tab,
    _build_low_row_count_warning,
    _find_all_option_value,
    _find_visible_company_name_input,
    _first_visible_matching_text,
    _get_selected_option,
    _is_excluded_control,
    _is_stocks_trading_data_active,
    _click_visible_market_watch_tab,
    _normalize_selection_text,
    _option_selection_matches,
    _describe_filter_kind_for_options,
    _build_fingerprint_unchanged_warning,
    _table_fingerprint,
    _visible_market_watch_selects,
    WarningDeduper,
    filter_report_warnings,
    _is_low_row_count,
    collect_browser_pages,
    collect_stocks_by_all_sectors,
    dedupe_stocks_by_name,
    EGX_UPDATE_OVERLAY_TIMEOUT_WARNING,
    extract_stocks_table_from_html,
    extract_stocks_table_from_page,
    extract_tables_from_html,
    find_sector_select_with_options,
    first_visible,
    is_egx_prices_page_url,
    merge_sector_stock_rows,
    MULTI_SECTOR_AFTER_DEDUPE_WARNING,
    MULTI_SECTOR_BEFORE_DEDUPE_WARNING,
    MULTI_SECTOR_COLLECTED_WARNING,
    MULTI_SECTOR_FAILED_VISIBLE_TABLE_FALLBACK_WARNING,
    MULTI_SECTOR_SECTOR_SKIPPED_WARNING,
    MULTI_SECTOR_UNAVAILABLE_FALLBACK_WARNING,
    NETWORK_IDLE_TIMEOUT_MS,
    normalize_browser_stocks_csv,
    prepare_full_market_stocks_view,
    print_table_detection_diagnostics,
    select_egx_prices_page,
    select_stocks_table,
    summarize_tables_in_html,
    tag_rows_with_sector,
    wait_for_egx_update_complete,
    wait_for_stocks_table_in_dom,
)

STOCKS_HTML = """
<html><body>
<table>
  <tr><th>Index</th><th>Last</th><th>Change</th></tr>
  <tr><td>EGX30</td><td>28000</td><td>1.2</td></tr>
</table>
<table>
  <tr>
    <th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>%CHG</th>
    <th>Last Price</th><th>High</th><th>Low</th>
  </tr>
  <tr>
    <td>COMI</td><td>80.0</td><td>79.5</td><td>80.2</td><td>1.2</td>
    <td>80.5</td><td>81.0</td><td>79.0</td>
  </tr>
  <tr>
    <td>SWDY</td><td>45.0</td><td>44.8</td><td>45.1</td><td>0.5</td>
    <td>45.2</td><td>45.5</td><td>44.5</td>
  </tr>
</table>
</body></html>
"""


@pytest.fixture
def reader(tmp_path: Path) -> EgxPublicBrowserStocksReader:
    return EgxPublicBrowserStocksReader(tmp_path)


@pytest.fixture
def attached_reader(tmp_path: Path) -> EgxAttachedChromeStocksReader:
    return EgxAttachedChromeStocksReader(tmp_path)


def test_is_egx_prices_page_url_matches_prices_variants() -> None:
    assert is_egx_prices_page_url("https://egx.com.eg/en/prices.aspx")
    assert is_egx_prices_page_url("https://egx.com.eg/en/Prices.aspx?x=1")
    assert not is_egx_prices_page_url("https://egx.com.eg/en/Indices.aspx")


def test_select_egx_prices_page_finds_matching_tab() -> None:
    egx_page = MagicMock()
    egx_page.url = "https://egx.com.eg/en/prices.aspx"
    other_page = MagicMock()
    other_page.url = "https://example.com/"

    selected = select_egx_prices_page([other_page, egx_page])

    assert selected is egx_page


def test_select_egx_prices_page_returns_none_when_missing() -> None:
    other_page = MagicMock()
    other_page.url = "https://example.com/"

    assert select_egx_prices_page([other_page]) is None


def test_collect_browser_pages_gathers_all_context_pages() -> None:
    page_one = MagicMock()
    page_two = MagicMock()
    context = MagicMock()
    context.pages = [page_one, page_two]
    browser = MagicMock()
    browser.contexts = [context]

    pages = collect_browser_pages(browser)

    assert pages == [page_one, page_two]


def test_extract_stocks_table_from_html_selects_stock_table() -> None:
    table = extract_stocks_table_from_html(STOCKS_HTML)

    assert table is not None
    assert "Name" in table.columns
    assert len(table) == 2


def test_read_with_playwright_attaches_and_reads(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    egx_page = MagicMock()
    egx_page.url = "https://egx.com.eg/en/prices.aspx"
    egx_page.content.return_value = STOCKS_HTML
    context = MagicMock()
    context.pages = [egx_page]
    browser = MagicMock()
    browser.contexts = [context]

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    mock_sync_playwright = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = playwright

    with patch.object(
        attached_reader,
        "_timestamp",
        return_value="20260701_120000",
    ), patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=extract_stocks_table_from_html(STOCKS_HTML),
    ):
        result = attached_reader._read_with_playwright(mock_sync_playwright)

    assert result.success is True
    assert result.saved_csv == (
        attached_reader.downloads_dir
        / "attached_chrome_stocks_20260701_120000.csv"
    )
    playwright.chromium.connect_over_cdp.assert_called_once_with(DEFAULT_CHROME_CDP_URL)
    browser.close.assert_called_once()
    assert egx_page.content.call_count >= 1


def test_read_with_playwright_returns_error_when_no_matching_page(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    other_page = MagicMock()
    other_page.url = "https://example.com/"
    context = MagicMock()
    context.pages = [other_page]
    browser = MagicMock()
    browser.contexts = [context]

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    mock_sync_playwright = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = playwright

    result = attached_reader._read_with_playwright(mock_sync_playwright)

    assert result.success is False
    assert result.errors == [NO_EGX_PRICES_PAGE_ERROR]


def test_read_with_playwright_returns_error_when_no_table(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    egx_page = MagicMock()
    egx_page.url = "https://egx.com.eg/en/prices.aspx"
    egx_page.content.return_value = "<html><body><p>No table</p></body></html>"
    context = MagicMock()
    context.pages = [egx_page]
    browser = MagicMock()
    browser.contexts = [context]

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    mock_sync_playwright = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = playwright

    with patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=None,
    ):
        result = attached_reader._read_with_playwright(mock_sync_playwright)

    assert result.success is False
    assert result.errors == [NO_STOCKS_TABLE_ERROR]


def test_open_or_select_prices_page_uses_existing_page(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    egx_page = MagicMock()
    egx_page.url = "https://egx.com.eg/en/prices.aspx"
    context = MagicMock()
    context.pages = [egx_page]
    browser = MagicMock()
    browser.contexts = [context]

    page, action, warnings, errors = attached_reader._open_or_select_prices_page(
        browser
    )

    assert page is egx_page
    assert action == "selected"
    assert errors == []
    context.new_page.assert_not_called()


def test_open_or_select_prices_page_opens_new_page_when_missing(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    other_page = MagicMock()
    other_page.url = "https://example.com/"
    new_page = MagicMock()
    new_page.url = "https://egx.com.eg/en/prices.aspx"
    context = MagicMock()
    context.pages = [other_page]
    context.new_page.return_value = new_page
    browser = MagicMock()
    browser.contexts = [context]

    page, action, warnings, errors = attached_reader._open_or_select_prices_page(
        browser
    )

    assert page is new_page
    assert action == "opened"
    assert errors == []
    context.new_page.assert_called_once()
    new_page.goto.assert_called_once_with(
        EGX_BROWSER_STOCKS_URL,
        wait_until="domcontentloaded",
        timeout=PAGE_LOAD_TIMEOUT_MS,
    )


def test_read_or_open_with_playwright_opens_page_and_reads(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    new_page = MagicMock()
    new_page.url = "https://egx.com.eg/en/prices.aspx"
    new_page.content.return_value = STOCKS_HTML
    context = MagicMock()
    context.pages = []
    context.new_page.return_value = new_page
    browser = MagicMock()
    browser.contexts = [context]

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    mock_sync_playwright = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = playwright

    with patch.object(
        attached_reader,
        "_timestamp",
        return_value="20260701_120000",
    ):
        result = attached_reader._read_or_open_with_playwright(mock_sync_playwright)

    assert result.success is True
    assert result.page_action == "opened"
    assert result.rows == 2


def test_is_low_row_count_uses_threshold() -> None:
    assert _is_low_row_count(99) is True
    assert _is_low_row_count(100) is False
    assert _is_low_row_count(LOW_ROW_COUNT_THRESHOLD - 1) is True


def test_build_low_row_count_warning_message() -> None:
    assert _build_low_row_count_warning(42) == LOW_ROW_COUNT_WARNING


def test_find_all_option_value_prefers_all_like_options() -> None:
    assert _find_all_option_value(["Banks", "All Sectors", "Insurance"]) == "All Sectors"
    assert _find_all_option_value(["Retail", "ALL", "Telecom"]) == "ALL"
    assert _find_all_option_value(["Banks", "Insurance"]) is None


def test_save_stocks_table_from_html_adds_low_row_count_warning(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    with patch.object(attached_reader, "_timestamp", return_value="20260701_120000"):
        result = attached_reader._save_stocks_table_from_html(
            STOCKS_HTML,
            [],
            page_action="selected",
        )

    assert result.success is True
    assert result.rows == 2
    assert LOW_ROW_COUNT_WARNING in result.warnings


def test_prepare_full_market_stocks_view_returns_warnings_without_hard_failure() -> None:
    page = MagicMock()
    page.url = "https://egx.com.eg/eg/en/prices.aspx"
    page.evaluate.return_value = False
    company_input = MagicMock()
    company_input.is_visible.return_value = True
    company_input.is_editable.return_value = True
    company_input.evaluate.return_value = {
        "id": "ctl00_ContentPlaceHolder1_txtCompany",
        "name": "company",
        "tag": "input",
        "href": "",
        "placeholder": "Enter Part Of The Company Name",
        "className": "Normaltextbox",
        "text": "",
    }
    segment_select = MagicMock()
    segment_select.is_visible.return_value = True
    segment_select.evaluate.return_value = ["All Market Segments", "Retail"]
    segment_select.select_option.side_effect = RuntimeError("missing dropdown")

    text_locator = MagicMock()
    text_locator.count.return_value = 1
    text_locator.nth.return_value = company_input
    select_locator = MagicMock()
    select_locator.count.return_value = 1
    select_locator.nth.return_value = segment_select

    def fake_locator(selector: str) -> MagicMock:
        if "select" in selector:
            return select_locator
        return text_locator

    page.locator.side_effect = fake_locator

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._is_stocks_trading_data_active",
        return_value=False,
    ):
        warnings = prepare_full_market_stocks_view(page)

    assert any(RESET_FILTER_WARNING_PREFIX in warning for warning in warnings)
    assert page.evaluate.call_count >= 1


def test_prepare_full_market_stocks_view_resets_all_option_when_available() -> None:
    page = MagicMock()
    page.url = "https://egx.com.eg/en/prices.aspx"
    page.evaluate.return_value = False
    company_input = MagicMock()
    company_input.is_visible.return_value = True
    company_input.is_editable.return_value = True
    company_input.evaluate.return_value = {
        "id": "ctl00_ContentPlaceHolder1_txtCompany",
        "placeholder": "Enter Part Of The Company Name",
        "name": "",
        "tag": "input",
        "href": "",
        "className": "",
        "text": "",
    }
    segment_select = MagicMock()
    segment_select.is_visible.return_value = True

    def segment_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return "All Market Segments"
        if "Array.from(el.options)" in script:
            return ["All Market Segments", "Retail"]
        return {}

    segment_select.evaluate.side_effect = segment_evaluate

    text_locator = MagicMock()
    text_locator.count.return_value = 1
    text_locator.nth.return_value = company_input
    select_locator = MagicMock()
    select_locator.count.return_value = 1
    select_locator.nth.return_value = segment_select

    def fake_locator(selector: str) -> MagicMock:
        if "select" in selector:
            return select_locator
        return text_locator

    page.locator.side_effect = fake_locator

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._is_stocks_trading_data_active",
        return_value=False,
    ):
        warnings = prepare_full_market_stocks_view(page)

    segment_select.select_option.assert_called_once_with(value="All Market Segments")
    assert not any("no All option found" in warning for warning in warnings)


def test_read_or_open_with_playwright_includes_reset_warnings(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    new_page = MagicMock()
    new_page.url = "https://egx.com.eg/en/prices.aspx"
    new_page.content.return_value = STOCKS_HTML
    context = MagicMock()
    context.pages = []
    context.new_page.return_value = new_page
    browser = MagicMock()
    browser.contexts = [context]

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    mock_sync_playwright = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = playwright

    reset_warning = (
        f"{RESET_FILTER_WARNING_PREFIX} no All option found for sector dropdown"
    )
    with patch.object(
        attached_reader,
        "_timestamp",
        return_value="20260701_120000",
    ), patch(
        "core.egx_browser_reader.prepare_full_market_stocks_view",
        return_value=[reset_warning],
    ), patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=extract_stocks_table_from_html(STOCKS_HTML),
    ):
        result = attached_reader._read_or_open_with_playwright(mock_sync_playwright)

    assert result.success is True
    assert reset_warning in result.warnings
    assert LOW_ROW_COUNT_WARNING in result.warnings


def test_browser_stocks_urls_use_non_www_primary() -> None:
    assert EGX_BROWSER_STOCKS_URL == "https://egx.com.eg/en/prices.aspx"
    assert EGX_BROWSER_STOCKS_URLS == (
        "https://egx.com.eg/en/prices.aspx",
        "https://egx.com.eg/en/Prices.aspx",
    )
    assert all("www.egx.com.eg" not in url for url in EGX_BROWSER_STOCKS_URLS)


def test_launch_browser_prefers_installed_chrome_channel(
    reader: EgxPublicBrowserStocksReader,
) -> None:
    playwright = MagicMock()
    browser = MagicMock()
    playwright.chromium.launch.return_value = browser

    launched = reader._launch_browser(playwright)

    assert launched is browser
    playwright.chromium.launch.assert_called_once_with(
        channel="chrome",
        headless=True,
        args=EGX_BROWSER_LAUNCH_ARGS,
    )


def test_launch_browser_falls_back_to_bundled_chromium(
    reader: EgxPublicBrowserStocksReader,
) -> None:
    playwright = MagicMock()
    bundled_browser = MagicMock()
    playwright.chromium.launch.side_effect = [
        RuntimeError("Chrome channel not found"),
        bundled_browser,
    ]

    launched = reader._launch_browser(playwright)

    assert launched is bundled_browser
    assert playwright.chromium.launch.call_count == 2
    assert playwright.chromium.launch.call_args_list[0].kwargs["channel"] == "chrome"
    fallback_call = playwright.chromium.launch.call_args_list[1]
    assert "channel" not in fallback_call.kwargs
    assert fallback_call.kwargs["headless"] is True
    assert fallback_call.kwargs["args"] == EGX_BROWSER_LAUNCH_ARGS


def test_fetch_stocks_page_html_tries_fallback_url(
    reader: EgxPublicBrowserStocksReader,
) -> None:
    page = MagicMock()
    page.content.return_value = STOCKS_HTML
    page.goto.side_effect = [
        ConnectionResetError(10054, "connection reset"),
        None,
    ]

    html, loaded_url = reader._fetch_stocks_page_html(page, [])

    assert html == STOCKS_HTML
    assert loaded_url == EGX_BROWSER_STOCKS_URLS[1]
    assert page.goto.call_count == 2
    assert page.goto.call_args_list[0].args[0] == EGX_BROWSER_STOCKS_URLS[0]
    assert page.goto.call_args_list[1].args[0] == EGX_BROWSER_STOCKS_URLS[1]


def test_extract_tables_from_html_returns_dataframes() -> None:
    tables = extract_tables_from_html(STOCKS_HTML)

    assert len(tables) == 2
    assert all(isinstance(table, pd.DataFrame) for table in tables)
    assert len(tables[1]) == 2


def test_select_stocks_table_prefers_stock_like_columns() -> None:
    tables = extract_tables_from_html(STOCKS_HTML)

    selected = select_stocks_table(tables)

    assert selected is not None
    assert "Name" in selected.columns
    assert "Last Price" in selected.columns
    assert len(selected) == 2


def test_normalize_browser_stocks_csv_creates_required_columns(tmp_path: Path) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI"],
            "Open": [79.5, 80.0],
            "Close": [80.2, 80.8],
            "Last Price": [80.5, 81.0],
            "High": [81.0, 81.5],
            "Low": [79.0, 79.5],
            "Date": ["2026-01-01", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert list(frame.columns) == [
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert frame.loc[0, "symbol"] == "COMI"
    assert frame.loc[0, "close"] == 80.5
    assert frame.loc[1, "close"] == 81.0
    assert frame.loc[0, "volume"] == 0
    assert LAST_PRICE_CLOSE_WARNING in result.ohlcv.warnings


def test_normalize_browser_stocks_csv_uses_last_price_when_close_also_exists(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI"],
            "Open": [79.5, 80.0],
            "Close": [99.9, 100.0],
            "Last Price": [80.5, 81.0],
            "High": [81.0, 81.5],
            "Low": [79.0, 79.5],
            "Date": ["2026-01-01", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert frame.loc[0, "close"] == 80.5
    assert frame.loc[1, "close"] == 81.0
    assert LAST_PRICE_CLOSE_WARNING in result.ohlcv.warnings


def test_normalize_browser_stocks_csv_validates_last_price_not_table_close(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI"],
            "Open": [80.0, 81.0],
            "Close": [85.0, 90.0],
            "Last Price": [80.5, 81.5],
            "High": [81.0, 82.0],
            "Low": [79.0, 80.0],
            "Date": ["2026-01-01", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert frame.loc[0, "close"] == 80.5
    assert frame.loc[0, "high"] >= frame.loc[0, "close"]
    assert frame.loc[0, "low"] <= frame.loc[0, "close"]


def test_normalize_browser_stocks_csv_saves_single_day_live_snapshot(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    snapshot_csv = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "HRHO"],
            "P.C.": [80.2, 10.1],
            "Open": [79.5, 10.0],
            "Last Price": [80.5, 10.4],
            "High": [81.0, 10.6],
            "Low": [79.0, 9.8],
            "Date": ["2026-01-07", "2026-01-07"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv, snapshot_csv)

    assert snapshot_csv.exists()
    assert result.live_snapshot is not None
    assert result.live_snapshot.valid is True
    assert result.ohlcv.valid is False
    assert any(
        "fewer than 2 dates" in error for error in result.ohlcv.errors
    )
    snapshot = pd.read_csv(snapshot_csv)
    assert list(snapshot.columns) == LIVE_SNAPSHOT_OUTPUT_COLUMNS
    assert len(snapshot) == 2


def test_normalize_browser_stocks_csv_maps_company_names_to_tickers(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    snapshot_csv = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        {
            "Name": ["Commercial International Bank-Egypt (CIB)", "Fawry"],
            "P.C.": [80.2, 6.0],
            "Open": [79.5, 5.9],
            "Last Price": [80.5, 6.1],
            "High": [81.0, 6.2],
            "Low": [79.0, 5.8],
            "Date": ["2026-01-07", "2026-01-07"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv, snapshot_csv)

    snapshot = pd.read_csv(snapshot_csv)
    assert set(snapshot["symbol"]) == {"COMI", "FWRY"}
    assert (
        snapshot.loc[snapshot["symbol"] == "COMI", "company_name"].iloc[0]
        == "Commercial International Bank-Egypt (CIB)"
    )
    assert result.symbol_mapping is not None
    assert result.symbol_mapping.mapped_rows == 2


def test_normalize_browser_stocks_csv_maps_pc_to_previous_close(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    snapshot_csv = tmp_path / "egx_live_snapshot.csv"
    pd.DataFrame(
        {
            "Name": ["COMI"],
            "P.C.": [80.2],
            "Open": [79.5],
            "Last Price": [80.5],
            "High": [81.0],
            "Low": [79.0],
            "Date": ["2026-01-07"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv, snapshot_csv)

    snapshot = pd.read_csv(snapshot_csv)
    assert result.live_snapshot is not None
    assert snapshot.loc[0, "previous_close"] == 80.2
    assert snapshot.loc[0, "close"] == 80.5
    assert snapshot.loc[0, "open"] == 79.5


def test_normalize_browser_stocks_csv_drops_invalid_ohlc_row(tmp_path: Path) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI", "COMI"],
            "Open": [80.0, 80.0, 80.0],
            "Last Price": [85.0, 80.5, 80.5],
            "High": [81.0, 81.0, 81.0],
            "Low": [79.0, 79.0, 82.0],
            "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    frame = pd.read_csv(output_csv)
    assert len(frame) == 1
    assert frame.loc[0, "date"] == "2026-01-02"
    assert frame.loc[0, "close"] == 80.5
    assert set(frame["date"]) == {"2026-01-02"}


def test_normalize_browser_stocks_csv_keeps_valid_rows_after_invalid_ohlc_drop(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI", "COMI"],
            "Open": [80.0, 80.0, 81.0],
            "Last Price": [85.0, 80.5, 81.5],
            "High": [81.0, 81.0, 82.0],
            "Low": [79.0, 79.0, 80.0],
            "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert len(frame) == 2
    assert set(frame["date"]) == {"2026-01-02", "2026-01-03"}


def test_normalize_browser_stocks_csv_warns_invalid_ohlc_drop_count(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI", "COMI"],
            "Open": [80.0, 80.0, 80.0],
            "Last Price": [85.0, 80.5, 80.5],
            "High": [81.0, 81.0, 81.0],
            "Low": [79.0, 79.0, 82.0],
            "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert INVALID_OHLC_RANGE_WARNING.format(count=2) in result.ohlcv.warnings


def test_normalize_browser_stocks_csv_skips_header_rows(tmp_path: Path) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": [
                "Egyptian Pound",
                "US Dollar ($)",
                "",
                "COMI",
                "COMI",
            ],
            "Open": ["-", "N/A", "", 79.5, 80.0],
            "High": ["-", "N/A", "", 81.0, 81.5],
            "Low": ["-", "N/A", "", 79.0, 79.5],
            "Last Price": ["-", "N/A", "", 80.5, 81.0],
            "Date": ["2026-01-01", "2026-01-01", "2026-01-01", "2026-01-01", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert len(frame) == 2
    assert set(frame["symbol"]) == {"COMI"}
    assert any(
        "Dropped 3 non-stock/header rows during normalization." in warning
        for warning in result.ohlcv.warnings
    )


def test_normalize_browser_stocks_csv_removes_comma_separated_numbers(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI"],
            "Open": ["1,234.5", "1,300.0"],
            "High": ["1,250.0", "1,320.5"],
            "Low": ["1,200.0", "1,280.0"],
            "Last Price": ["1,245.5", "1,310.0"],
            "Date": ["2026-01-01", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert frame.loc[0, "open"] == 1234.5
    assert frame.loc[0, "close"] == 1245.5


def test_normalize_browser_stocks_csv_succeeds_after_bad_rows(tmp_path: Path) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["Section Header", "COMI", "COMI", "US Dollar"],
            "Open": ["Open", 79.5, 80.0, "1.00"],
            "High": ["High", 81.0, 81.5, "1.00"],
            "Low": ["Low", 79.0, 79.5, "1.00"],
            "Last Price": ["Last Price", 80.5, 81.0, "1.00"],
            "Date": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    frame = pd.read_csv(output_csv)
    assert len(frame) == 2
    assert "Dropped 2 non-stock/header rows during normalization." in result.ohlcv.warnings


def test_normalize_browser_stocks_csv_warns_when_volume_missing(tmp_path: Path) -> None:
    input_csv = tmp_path / "browser_stocks.csv"
    output_csv = tmp_path / "normalized.csv"
    pd.DataFrame(
        {
            "Name": ["COMI", "COMI"],
            "Open": [79.5, 80.0],
            "High": [81.0, 81.5],
            "Low": [79.0, 79.5],
            "Last Price": [80.5, 81.0],
            "Date": ["2026-01-01", "2026-01-02"],
        }
    ).to_csv(input_csv, index=False)

    result = normalize_browser_stocks_csv(input_csv, output_csv)

    assert result.ohlcv.valid is True
    assert VOLUME_MISSING_WARNING in result.ohlcv.warnings
    frame = pd.read_csv(output_csv)
    assert frame.loc[0, "volume"] == 0


def test_build_symbol_count_warnings_critical() -> None:
    from core.egx_browser_reader import build_symbol_count_warnings

    warnings = build_symbol_count_warnings(50, warn_threshold=150, critical_threshold=80)

    assert len(warnings) == 1
    assert "50" in warnings[0]


def test_build_symbol_count_warnings_warn_only() -> None:
    from core.egx_browser_reader import build_symbol_count_warnings

    warnings = build_symbol_count_warnings(120, warn_threshold=150, critical_threshold=80)

    assert len(warnings) == 1
    assert "120" in warnings[0]


def test_build_symbol_count_warnings_ok() -> None:
    from core.egx_browser_reader import build_symbol_count_warnings

    assert build_symbol_count_warnings(200, warn_threshold=150, critical_threshold=80) == []


def _stock_table(names: list[str], *, sector: str | None = None) -> pd.DataFrame:
    rows = []
    for name in names:
        row = {
            "Name": name,
            "P.C.": 80.0,
            "Open": 79.5,
            "Close": 80.2,
            "Last Price": 80.5,
            "High": 81.0,
            "Low": 79.0,
        }
        if sector is not None:
            row["Sector"] = sector
        rows.append(row)
    return pd.DataFrame(rows)


def test_find_sector_select_with_options_collects_sector_dropdown() -> None:
    page = MagicMock()
    banks_select = MagicMock()
    banks_select.is_visible.return_value = True
    banks_select.evaluate.return_value = ["Banks", "Insurance", "Telecom"]
    traded_select = MagicMock()
    traded_select.is_visible.return_value = True
    traded_select.evaluate.return_value = ["EGX 30", "EGX 70", "EGX 100"]
    page.locator.return_value.count.return_value = 2
    page.locator.return_value.nth.side_effect = [banks_select, traded_select]

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ) as activate_tab:
        sector_select, options = find_sector_select_with_options(page)

    activate_tab.assert_called_once_with(page, SECTOR_TAB_TEXT, [])
    assert sector_select is banks_select
    assert options == ["Banks", "Insurance", "Telecom"]


def test_merge_sector_stock_rows_combines_all_frames() -> None:
    merged = merge_sector_stock_rows(
        [
            _stock_table(["COMI"], sector="Banks"),
            _stock_table(["SWDY"], sector="Telecom"),
        ]
    )

    assert len(merged) == 2
    assert set(merged["Name"]) == {"COMI", "SWDY"}


def test_dedupe_stocks_by_name_keeps_first_occurrence() -> None:
    merged = merge_sector_stock_rows(
        [
            _stock_table(["COMI", "HRHO"], sector="Banks"),
            _stock_table(["COMI"], sector="Insurance"),
        ]
    )

    deduped = dedupe_stocks_by_name(merged)

    assert len(deduped) == 2
    assert set(deduped["Name"]) == {"COMI", "HRHO"}


def test_tag_rows_with_sector_adds_sector_column() -> None:
    tagged = tag_rows_with_sector(_stock_table(["COMI"]), "Banks")

    assert "Sector" in tagged.columns
    assert tagged.loc[0, "Sector"] == "Banks"


def test_collect_stocks_by_all_sectors_merges_and_warns() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    sector_select = MagicMock()
    sector_select.is_visible.return_value = True
    current_sector = {"name": "Banks"}

    def sector_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return current_sector["name"]
        if "Array.from(el.options)" in script:
            return ["Banks", "Insurance", "Select"]
        return {}

    sector_select.evaluate.side_effect = sector_evaluate
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = sector_select

    html_by_sector = {
        "Banks": """
        <html><body><table>
          <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
          <tr><td>COMI</td><td>80</td><td>79.5</td><td>80.2</td><td>80.5</td><td>81</td><td>79</td></tr>
          <tr><td>HRHO</td><td>10</td><td>9.8</td><td>10.1</td><td>10.2</td><td>10.4</td><td>9.7</td></tr>
        </table></body></html>
        """,
        "Insurance": """
        <html><body><table>
          <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
          <tr><td>COMI</td><td>80</td><td>79.5</td><td>80.2</td><td>80.5</td><td>81</td><td>79</td></tr>
          <tr><td>PHDC</td><td>5</td><td>4.9</td><td>5.0</td><td>5.1</td><td>5.2</td><td>4.8</td></tr>
        </table></body></html>
        """,
    }

    def fake_select_option(*args, **kwargs):
        current_sector["name"] = kwargs.get("value") or kwargs.get("label") or "Banks"

    sector_select.select_option.side_effect = fake_select_option
    page.content.side_effect = lambda: html_by_sector[current_sector["name"]]

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ):
        table, warnings, attempted = collect_stocks_by_all_sectors(page)

    assert attempted is True
    assert table is not None
    assert len(table) == 3
    assert set(table["Name"]) == {"COMI", "HRHO", "PHDC"}
    assert MULTI_SECTOR_COLLECTED_WARNING.format(count=2) in warnings
    assert MULTI_SECTOR_BEFORE_DEDUPE_WARNING.format(count=4) in warnings
    assert MULTI_SECTOR_AFTER_DEDUPE_WARNING.format(count=3) in warnings


def test_read_stocks_table_from_page_falls_back_to_single_table(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    page = MagicMock()
    page.content.return_value = STOCKS_HTML

    with patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=extract_stocks_table_from_html(STOCKS_HTML),
    ):
        result = attached_reader._read_stocks_table_from_page(page, [], page_action="selected")

    assert result.success is True
    assert result.rows == 2
    assert MULTI_SECTOR_UNAVAILABLE_FALLBACK_WARNING in result.warnings


def test_network_idle_timeout_is_sixty_seconds() -> None:
    assert NETWORK_IDLE_TIMEOUT_MS == 60_000


def test_wait_for_egx_update_complete_waits_for_overlay_to_disappear() -> None:
    page = MagicMock()
    updating = MagicMock()
    updating.count.return_value = 1
    updating.first.is_visible.return_value = True
    updating.wait_for.return_value = None
    page.get_by_text.return_value = updating

    assert wait_for_egx_update_complete(page) is True
    updating.wait_for.assert_called_once_with(state="hidden", timeout=60_000)


def test_wait_for_egx_update_complete_warns_on_stuck_overlay() -> None:
    page = MagicMock()
    updating = MagicMock()
    updating.count.return_value = 1
    updating.first.is_visible.return_value = True
    updating.wait_for.side_effect = RuntimeError("timeout")
    page.get_by_text.return_value = updating
    page.wait_for_selector.side_effect = [RuntimeError("no table"), None]
    page.content.return_value = STOCKS_HTML

    warnings: list[str] = []
    assert wait_for_egx_update_complete(page, warnings=warnings) is True
    assert EGX_UPDATE_OVERLAY_TIMEOUT_WARNING in warnings
    page.keyboard.press.assert_called_with("Escape")


def test_collect_stocks_by_all_sectors_skips_stuck_sector() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    sector_select = MagicMock()
    sector_select.is_visible.return_value = True
    current_sector = {"name": "Banks"}

    def sector_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return current_sector["name"]
        if "Array.from(el.options)" in script:
            return ["Banks", "Insurance", "Select"]
        return {}

    sector_select.evaluate.side_effect = sector_evaluate
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = sector_select

    banks_html = """
    <html><body><table>
      <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
      <tr><td>COMI</td><td>80</td><td>79.5</td><td>80.2</td><td>80.5</td><td>81</td><td>79</td></tr>
    </table></body></html>
    """
    current_sector = {"name": "Banks"}

    def fake_select_option(*args, **kwargs):
        current_sector["name"] = kwargs.get("value") or kwargs.get("label") or "Banks"

    sector_select.select_option.side_effect = fake_select_option

    def fake_content() -> str:
        if current_sector["name"] == "Insurance":
            return "<html><body><div>Updating ...</div></body></html>"
        return banks_html

    page.content.side_effect = fake_content

    with patch(
        "core.egx_browser_reader.wait_for_egx_update_complete",
        side_effect=lambda page_arg, warnings=None, timeout_ms=45_000: (
            current_sector["name"] != "Insurance"
        ),
    ), patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ):
        table, warnings, attempted = collect_stocks_by_all_sectors(page)

    assert attempted is True
    assert table is not None
    assert len(table) == 1
    assert MULTI_SECTOR_SECTOR_SKIPPED_WARNING.format(sector="Insurance") in warnings


def test_read_stocks_table_from_page_uses_visible_table_when_multi_sector_fails(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    page = MagicMock()
    page.content.return_value = STOCKS_HTML

    with patch(
        "core.egx_browser_reader.collect_stocks_by_all_sectors",
        return_value=(None, [], True),
    ), patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=extract_stocks_table_from_html(STOCKS_HTML),
    ):
        result = attached_reader._read_stocks_table_from_page(
            page,
            [],
            page_action="selected",
        )

    assert result.success is True
    assert result.rows == 2
    assert MULTI_SECTOR_FAILED_VISIBLE_TABLE_FALLBACK_WARNING in result.warnings


def test_summarize_tables_in_html_reports_dom_tables() -> None:
    summaries = summarize_tables_in_html(STOCKS_HTML)

    assert len(summaries) == 2
    assert summaries[1]["rows"] == 2
    assert summaries[1]["score"] > summaries[0]["score"]


def test_extract_stocks_table_from_page_uses_dom_not_visibility() -> None:
    page = MagicMock()
    page.content.return_value = STOCKS_HTML

    with patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=None,
    ):
        table = extract_stocks_table_from_page(page)

    assert table is not None
    assert len(table) == 2
    page.content.assert_called()


def test_print_table_detection_diagnostics_includes_table_preview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = MagicMock()
    page.url = "https://egx.com.eg/en/prices.aspx"
    page.content.return_value = STOCKS_HTML
    page.locator.return_value.count.return_value = 0

    print_table_detection_diagnostics(page)
    output = capsys.readouterr().out

    assert "EGX table diagnostics:" in output
    assert "Tables in DOM: 2" in output
    assert "Name" in output
    assert "EGX control diagnostics:" in output


def _mock_input(
    *,
    element_id: str = "",
    placeholder: str = "",
    visible: bool = True,
    editable: bool = True,
    href: str = "",
    tag: str = "input",
    text: str = "",
) -> MagicMock:
    element = MagicMock()
    element.is_visible.return_value = visible
    element.is_editable.return_value = editable
    element.evaluate.return_value = {
        "id": element_id,
        "name": "",
        "tag": tag,
        "href": href,
        "placeholder": placeholder,
        "className": "",
        "text": text,
    }
    return element


def test_first_visible_ignores_hidden_header_search_input() -> None:
    hidden = _mock_input(
        element_id=HEADER_SEARCH_INPUT_ID,
        placeholder="Search",
        visible=True,
    )
    visible = _mock_input(
        element_id="ctl00_ContentPlaceHolder1_txtCompany",
        placeholder="Enter Part Of The Company Name",
        visible=True,
    )
    locator = MagicMock()
    locator.count.return_value = 2
    locator.nth.side_effect = [hidden, visible]
    warnings: list[str] = []

    selected = first_visible(locator, "company input", warnings, require_editable=True)

    assert selected is visible
    assert not warnings


def test_is_excluded_control_rejects_header_search_and_stocks_nav_link() -> None:
    header_search = _mock_input(element_id=HEADER_SEARCH_INPUT_ID)
    hidden_stocks = _mock_input(
        element_id="nav_stocks",
        href="/en/Stocks.aspx",
        tag="a",
        text="Stocks",
    )
    title_node = _mock_input(tag="title", text="Trading Data")

    assert _is_excluded_control(header_search) is True
    assert _is_excluded_control(hidden_stocks) is True
    assert _is_excluded_control(title_node) is True


def test_find_visible_company_name_input_prefers_company_placeholder() -> None:
    page = MagicMock()
    header_search = _mock_input(element_id=HEADER_SEARCH_INPUT_ID, placeholder="Search")
    company_input = _mock_input(
        element_id="ctl00_ContentPlaceHolder1_txtCompany",
        placeholder="Enter Part Of The Company Name",
    )
    page.locator.return_value.count.return_value = 2
    page.locator.return_value.nth.side_effect = [header_search, company_input]

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ):
        selected = _find_visible_company_name_input(page, [])

    assert selected is company_input


def test_first_visible_matching_text_ignores_hidden_stocks_nav_link() -> None:
    hidden_stocks = _mock_input(
        href="/en/Stocks.aspx",
        tag="a",
        text="Stocks",
        visible=False,
    )
    visible_tab = _mock_input(tag="span", text="Stocks", visible=True)
    locator = MagicMock()
    locator.count.return_value = 2
    locator.nth.side_effect = [hidden_stocks, visible_tab]
    warnings: list[str] = []

    selected = _first_visible_matching_text(
        locator,
        "Stocks",
        warnings,
        "Stocks tab",
        exact=True,
    )

    assert selected is visible_tab
    assert not warnings


def test_first_visible_matching_text_does_not_match_title_node() -> None:
    title_node = _mock_input(tag="title", text="Trading Data", visible=True)
    visible_tab = _mock_input(tag="a", text="Trading Data", visible=True)
    locator = MagicMock()
    locator.count.return_value = 2
    locator.nth.side_effect = [title_node, visible_tab]
    warnings: list[str] = []

    selected = _first_visible_matching_text(
        locator,
        "Trading Data",
        warnings,
        "Trading Data tab",
        exact=True,
    )

    assert selected is visible_tab


def test_click_visible_market_watch_tab_uses_js_evaluate() -> None:
    page = MagicMock()
    page.evaluate.return_value = True
    warnings: list[str] = []

    assert _click_visible_market_watch_tab(page, "Stocks", warnings, exact=True) is True
    page.evaluate.assert_called_once()


def test_visible_market_watch_selects_skips_hidden_dropdowns() -> None:
    page = MagicMock()
    hidden_select = MagicMock()
    hidden_select.is_visible.return_value = False
    visible_select = MagicMock()
    visible_select.is_visible.return_value = True
    page.locator.return_value.count.return_value = 2
    page.locator.return_value.nth.side_effect = [hidden_select, visible_select]

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ):
        visible = _visible_market_watch_selects(page)

    assert visible == [visible_select]


def test_collect_stocks_by_all_sectors_with_visible_controls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = MagicMock()
    page.evaluate.side_effect = [
        True,
        {
            "stocks": {"total": 1, "visible": 1},
            "trading_data": {"total": 1, "visible": 1},
            "inputs": [],
            "selects": [],
        },
        True,
    ]
    sector_select = MagicMock()
    sector_select.is_visible.return_value = True
    current_sector = {"name": "Banks"}

    def sector_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return current_sector["name"]
        if "Array.from(el.options)" in script:
            return ["Banks", "Telecom"]
        return {}

    sector_select.evaluate.side_effect = sector_evaluate
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = sector_select

    html_by_sector = {
        "Banks": """
        <html><body><table>
          <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
          <tr><td>COMI</td><td>80</td><td>79.5</td><td>80.2</td><td>80.5</td><td>81</td><td>79</td></tr>
        </table></body></html>
        """,
        "Telecom": """
        <html><body><table>
          <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
          <tr><td>ETEL</td><td>20</td><td>19.5</td><td>20.1</td><td>20.2</td><td>20.4</td><td>19.8</td></tr>
        </table></body></html>
        """,
    }

    def fake_select_option(*args, **kwargs):
        current_sector["name"] = kwargs.get("value") or kwargs.get("label") or "Banks"

    sector_select.select_option.side_effect = fake_select_option
    page.content.side_effect = lambda: html_by_sector[current_sector["name"]]

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ):
        table, warnings, attempted = collect_stocks_by_all_sectors(page)

    assert attempted is True
    assert table is not None
    assert set(table["Name"]) == {"COMI", "ETEL"}
    assert MULTI_SECTOR_COLLECTED_WARNING.format(count=2) in warnings


def test_sector_tab_activated_before_each_sector_selection() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    sector_select = MagicMock()
    sector_select.is_visible.return_value = True
    sector_select.evaluate.return_value = ["Banks", "Insurance"]
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = sector_select
    page.content.return_value = STOCKS_HTML

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ) as activate_tab, patch(
        "core.egx_browser_reader._select_and_verify_dropdown_option",
        return_value=(True, {"value": "Banks", "label": "Banks"}),
    ), patch(
        "core.egx_browser_reader.extract_stocks_table_from_page",
        return_value=extract_stocks_table_from_html(STOCKS_HTML),
    ):
        collect_stocks_by_all_sectors(page)

    sector_calls = [
        call.args[1]
        for call in activate_tab.call_args_list
        if len(call.args) > 1 and call.args[1] == SECTOR_TAB_TEXT
    ]
    assert len(sector_calls) >= 2


def test_market_segment_tab_activated_before_reset() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    segment_select = MagicMock()
    segment_select.is_visible.return_value = True
    segment_select.evaluate.return_value = ["All Market Segments", "Retail"]

    select_locator = MagicMock()
    select_locator.count.return_value = 1
    select_locator.nth.return_value = segment_select
    page.locator.return_value = select_locator

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ) as activate_tab, patch(
        "core.egx_browser_reader._clear_company_filter_if_visible",
    ):
        from core.egx_browser_reader import _reset_market_segment_filter

        _reset_market_segment_filter(page, [])

    assert any(
        len(call.args) > 1 and call.args[1] == MARKET_SEGMENT_TAB_TEXT
        for call in activate_tab.call_args_list
    )


def test_collect_stocks_skips_duplicate_sector_fingerprints() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    sector_select = MagicMock()
    sector_select.is_visible.return_value = True
    current_sector = {"name": "Banks"}

    def sector_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return current_sector["name"]
        if "Array.from(el.options)" in script:
            return ["Banks", "Insurance"]
        return {}

    sector_select.evaluate.side_effect = sector_evaluate
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = sector_select
    same_html = """
    <html><body><table>
      <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
      <tr><td>COMI</td><td>80</td><td>79.5</td><td>80.2</td><td>80.5</td><td>81</td><td>79</td></tr>
    </table></body></html>
    """

    def fake_select_option(*args, **kwargs):
        current_sector["name"] = kwargs.get("value") or kwargs.get("label") or "Banks"

    sector_select.select_option.side_effect = fake_select_option
    page.content.return_value = same_html

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ):
        table, warnings, attempted = collect_stocks_by_all_sectors(page)

    assert attempted is True
    assert table is not None
    assert len(table) == 1
    assert warnings.count(
        FILTER_DUPLICATE_FINGERPRINT_WARNING.format(
            filter_kind="Sector",
            filter_name="Insurance",
        )
    ) <= 1


def test_collect_stocks_warns_when_reused_table_detected() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    sector_select = MagicMock()
    sector_select.is_visible.return_value = True
    labels = [f"Sector{i}" for i in range(50)]
    sector_select.evaluate.return_value = labels
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = sector_select
    same_html = """
    <html><body><table>
      <tr><th>Name</th><th>P.C.</th><th>Open</th><th>Close</th><th>Last Price</th><th>High</th><th>Low</th></tr>
      <tr><td>COMI</td><td>80</td><td>79.5</td><td>80.2</td><td>80.5</td><td>81</td><td>79</td></tr>
      <tr><td>HRHO</td><td>10</td><td>9.8</td><td>10.1</td><td>10.2</td><td>10.4</td><td>9.7</td></tr>
    </table></body></html>
    """
    page.content.return_value = same_html
    fingerprint_counter = {"value": 0}

    def next_fingerprint(_df=None) -> str:
        fingerprint_counter["value"] += 1
        return f"fp:{fingerprint_counter['value']}"

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ), patch(
        "core.egx_browser_reader._select_and_verify_dropdown_option",
        side_effect=[(True, {"value": label, "label": label}) for label in labels],
    ), patch(
        "core.egx_browser_reader._table_fingerprint",
        side_effect=next_fingerprint,
    ), patch(
        "core.egx_browser_reader._table_fingerprint_from_page",
        side_effect=lambda page_arg: next_fingerprint(),
    ):
        _, warnings, attempted = collect_stocks_by_all_sectors(page)

    assert attempted is True
    assert MULTI_SECTOR_REUSED_TABLE_CRITICAL_WARNING in warnings


def test_company_filter_missing_emits_single_warning() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    page.locator.return_value.count.return_value = 0

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=False,
    ):
        from core.egx_browser_reader import _clear_company_filter_if_visible

        warnings: list[str] = []
        _clear_company_filter_if_visible(page, warnings)
        _clear_company_filter_if_visible(page, warnings)

    assert warnings.count(COMPANY_FILTER_UNAVAILABLE_WARNING) == 1


def test_ensure_stocks_trading_data_skips_when_already_active(
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = MagicMock()
    page.url = "https://egx.com.eg/en/prices.aspx"
    warnings: list[str] = []

    with patch(
        "core.egx_browser_reader._is_stocks_trading_data_active",
        return_value=True,
    ), patch(
        "core.egx_browser_reader._click_visible_market_watch_tab",
    ) as click_tab:
        from core.egx_browser_reader import ensure_stocks_trading_data_view

        ensure_stocks_trading_data_view(page, warnings)

    click_tab.assert_not_called()
    output = capsys.readouterr().out
    assert ALREADY_ON_STOCKS_TRADING_DATA_MESSAGE in output
    assert not any("could not click Stocks tab" in warning for warning in warnings)


def test_table_fingerprint_changes_when_rows_change() -> None:
    first = _stock_table(["COMI", "HRHO"])
    second = _stock_table(["COMI", "PHDC"])

    assert _table_fingerprint(first) != _table_fingerprint(second)


def test_option_selection_matches_value_one_with_label_banks() -> None:
    option_pairs = [{"value": "1", "label": "Banks"}]
    assert _option_selection_matches(
        "1",
        "Banks",
        "1",
        "Banks",
        option_pairs=option_pairs,
    )


def test_option_selection_matches_requested_label_only() -> None:
    assert _option_selection_matches(
        "",
        "Banks",
        "",
        "Banks",
    )


def test_option_selection_matches_normalizes_whitespace_and_case() -> None:
    assert _option_selection_matches(
        "1",
        "Health Care & Pharmaceuticals",
        "1",
        "health  care & pharmaceuticals",
    )
    assert _normalize_selection_text("  Basic   Resources ") == "basic resources"


def test_option_selection_matches_when_selected_value_matches_requested_value() -> None:
    page = MagicMock()
    select = MagicMock()
    select.select_option.return_value = None

    def fake_evaluate(script: str) -> object:
        if "dispatchEvent" in script:
            return None
        return {"value": "1", "label": "Banks"}

    select.evaluate.side_effect = fake_evaluate
    warnings: list[str] = []

    from core.egx_browser_reader import _select_and_verify_dropdown_option

    matched, selected = _select_and_verify_dropdown_option(
        page,
        select,
        "1",
        "Banks",
        warnings,
        option_pairs=[{"value": "1", "label": "Banks"}],
    )

    assert matched is True
    assert selected == {"value": "1", "label": "Banks"}
    assert not any("match=NO" in warning for warning in warnings)


def test_option_selection_mismatch_adds_warning_when_no_match() -> None:
    page = MagicMock()
    select = MagicMock()
    select.select_option.return_value = None

    def fake_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return {"value": "9", "label": "Telecom"}
        return {}

    select.evaluate.side_effect = fake_evaluate
    warnings: list[str] = []

    from core.egx_browser_reader import _select_and_verify_dropdown_option

    matched, _selected = _select_and_verify_dropdown_option(
        page,
        select,
        "1",
        "Banks",
        warnings,
        option_pairs=[{"value": "1", "label": "Banks"}],
    )

    assert matched is False
    assert any("match=NO" in warning for warning in warnings)


def test_read_stocks_table_from_page_does_not_fallback_when_some_sectors_succeed(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    page = MagicMock()
    page.content.return_value = STOCKS_HTML
    multi_table = extract_stocks_table_from_html(STOCKS_HTML)

    with patch(
        "core.egx_browser_reader.collect_stocks_by_all_sectors",
        return_value=(multi_table, ["Collected EGX stocks from 3 sectors"], True),
    ), patch(
        "core.egx_browser_reader.extract_stocks_table_from_page",
    ) as fallback_extract:
        result = attached_reader._read_stocks_table_from_page(
            page,
            [],
            page_action="selected",
        )

    assert result.success is True
    fallback_extract.assert_not_called()
    assert MULTI_SECTOR_FAILED_VISIBLE_TABLE_FALLBACK_WARNING not in result.warnings


def test_get_selected_option_reads_value_and_label() -> None:
    select = MagicMock()
    select.evaluate.return_value = {"value": "1", "label": "Banks"}

    selected = _get_selected_option(select)

    assert selected == {"value": "1", "label": "Banks"}


def test_submit_search_warning_is_deduped_per_phase() -> None:
    page = MagicMock()
    page.locator.return_value.count.return_value = 0
    page.get_by_role.return_value.count.return_value = 0
    deduper = WarningDeduper()
    warnings: list[str] = []

    from core.egx_browser_reader import _submit_search_after_clear

    _submit_search_after_clear(page, warnings, deduper=deduper, phase="sector_collect")
    _submit_search_after_clear(page, warnings, deduper=deduper, phase="sector_collect")

    assert warnings == [NO_VISIBLE_FILTER_SUBMIT_WARNING]
    assert not any("no visible Go button found" in warning for warning in warnings)


def test_filter_report_warnings_removes_noisy_submit_spam() -> None:
    filtered = filter_report_warnings(
        [
            NO_VISIBLE_FILTER_SUBMIT_WARNING,
            f"{RESET_FILTER_WARNING_PREFIX} no visible Go button found",
            f"{RESET_FILTER_WARNING_PREFIX} no visible Search button found",
            "Collected EGX stocks from 10 market segments",
        ]
    )

    assert filtered == [
        NO_VISIBLE_FILTER_SUBMIT_WARNING,
        "Collected EGX stocks from 10 market segments",
    ]


def test_describe_filter_kind_treats_index_options_as_market_segment() -> None:
    assert _describe_filter_kind_for_options(
        ["EGX 30", "EGX 70", "EGX 100"]
    ) == "market segment"
    assert _describe_filter_kind_for_options(
        ["Banks", "Basic Resources", "Telecom"]
    ) == "sector"


def test_fingerprint_unchanged_warning_uses_market_segment_label() -> None:
    warning = _build_fingerprint_unchanged_warning(
        "market segment",
        "EGX 30 (Index Companies)",
    )
    assert warning == MARKET_SEGMENT_FINGERPRINT_UNCHANGED_WARNING.format(
        segment="EGX 30 (Index Companies)"
    )


def test_fingerprint_unchanged_warning_uses_sector_label() -> None:
    warning = _build_fingerprint_unchanged_warning("sector", "Banks")
    assert warning == "Sector 'Banks' selected but table fingerprint did not change."


def test_market_segment_collection_summary_message() -> None:
    page = MagicMock()
    page.evaluate.return_value = False
    segment_select = MagicMock()
    segment_select.is_visible.return_value = True

    def segment_evaluate(script: str) -> object:
        if "selectedIndex" in script:
            return {"value": "1", "label": "EGX 30"}
        if "Array.from(el.options)" in script:
            return [
                {"value": "1", "label": "EGX 30"},
                {"value": "2", "label": "EGX 70"},
            ]
        return {}

    segment_select.evaluate.side_effect = segment_evaluate
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value = segment_select
    page.content.return_value = STOCKS_HTML

    with patch(
        "core.egx_browser_reader._market_watch_root_locator",
        return_value=None,
    ), patch(
        "core.egx_browser_reader._prepare_multi_sector_filters",
    ), patch(
        "core.egx_browser_reader._activate_filter_tab",
        return_value=True,
    ), patch(
        "core.egx_browser_reader._select_and_verify_dropdown_option",
        side_effect=[
            (True, {"value": "1", "label": "EGX 30"}),
            (True, {"value": "2", "label": "EGX 70"}),
        ],
    ), patch(
        "core.egx_browser_reader.extract_stocks_table_from_page",
        side_effect=[
            extract_stocks_table_from_html(STOCKS_HTML),
            extract_stocks_table_from_html(STOCKS_HTML),
        ],
    ), patch(
        "core.egx_browser_reader._table_fingerprint_from_page",
        side_effect=["fp:before1", "fp:after1", "fp:before2", "fp:after2"],
    ), patch(
        "core.egx_browser_reader._table_fingerprint",
        side_effect=["fp:after1", "fp:after2"],
    ):
        _, warnings, attempted = collect_stocks_by_all_sectors(page)

    assert attempted is True
    assert MULTI_MARKET_SEGMENT_COLLECTED_WARNING.format(count=2) in warnings
    assert MULTI_SECTOR_COLLECTED_WARNING.format(count=2) not in warnings


def test_save_stocks_table_skips_low_row_warning_after_full_market_collect(
    attached_reader: EgxAttachedChromeStocksReader,
) -> None:
    table = extract_stocks_table_from_html(STOCKS_HTML)
    warnings = [MULTI_MARKET_SEGMENT_COLLECTED_WARNING.format(count=18)]

    result = attached_reader._save_stocks_table_from_dataframe(
        table,
        warnings,
        page_action="selected",
    )

    assert result.success is True
    assert LOW_ROW_COUNT_WARNING not in result.warnings


def test_read_stocks_table_from_page_prints_diagnostics_when_missing(
    attached_reader: EgxAttachedChromeStocksReader,
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = MagicMock()
    page.url = "https://egx.com.eg/en/prices.aspx"
    page.content.return_value = "<html><body><p>No tables</p></body></html>"
    page.locator.return_value.count.return_value = 0

    with patch(
        "core.egx_browser_reader.wait_for_stocks_table_in_dom",
        return_value=None,
    ), patch(
        "core.egx_browser_reader.collect_stocks_by_all_sectors",
        return_value=(None, [], False),
    ):
        result = attached_reader._read_stocks_table_from_page(
            page,
            [],
            page_action="selected",
        )

    assert result.success is False
    output = capsys.readouterr().out
    assert "EGX table diagnostics:" in output
    assert "Tables in DOM: 0" in output
