"""Tests for per-symbol Sector Intelligence."""

from __future__ import annotations

import pandas as pd

from core.sector_intelligence import (
    LABEL_LEADER_IN_HOT_SECTOR,
    LABEL_STRONG_STOCK_WEAK_SECTOR,
    LABEL_SUPPORTED_BY_SECTOR,
    LABEL_UNKNOWN_SECTOR,
    LABEL_WEAK_IN_HOT_SECTOR,
    SectorIntelligenceInput,
    build_sector_intelligence_context,
    enrich_section_lines_with_sector_intelligence,
    format_sector_intelligence_arabic_block,
    format_sector_intelligence_report_lines,
    format_symbol_sector_intelligence_arabic_lines,
)
from core.sector_momentum import build_sector_momentum


def _snapshot_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "LEAD",
                "sector": "Real Estate",
                "change_percent": 3.2,
                "volume": 100_000,
                "volume_ratio": 2.0,
            },
            {
                "symbol": "GOOD",
                "sector": "Real Estate",
                "change_percent": 1.5,
                "volume": 80_000,
                "volume_ratio": 1.5,
            },
            {
                "symbol": "WEAK",
                "sector": "Real Estate",
                "change_percent": -0.4,
                "volume": 70_000,
                "volume_ratio": 1.2,
            },
            {
                "symbol": "SOLO",
                "sector": "Utilities",
                "change_percent": 2.2,
                "volume": 60_000,
                "volume_ratio": 1.0,
            },
            {
                "symbol": "DRAG",
                "sector": "Utilities",
                "change_percent": -2.0,
                "volume": 50_000,
                "volume_ratio": 0.9,
            },
            {
                "symbol": "DOWN",
                "sector": "Utilities",
                "change_percent": -3.0,
                "volume": 40_000,
                "volume_ratio": 0.8,
            },
        ]
    )


def test_sector_intelligence_labels_symbol_relationships() -> None:
    snapshot = _snapshot_df()
    sector_momentum = build_sector_momentum(snapshot)
    context, summary, available = build_sector_intelligence_context(
        [
            SectorIntelligenceInput("LEAD", score=90, change_pct=3.2),
            SectorIntelligenceInput("GOOD", score=80, change_pct=1.5),
            SectorIntelligenceInput("WEAK", score=45, change_pct=-0.4),
            SectorIntelligenceInput("SOLO", score=85, change_pct=2.2),
            SectorIntelligenceInput("MISSING", score=80, change_pct=1.0),
        ],
        snapshot_df=snapshot,
        sector_momentum=sector_momentum,
    )

    assert available is True
    assert context["LEAD"]["sector_label"] == LABEL_LEADER_IN_HOT_SECTOR
    assert context["GOOD"]["sector_label"] == LABEL_SUPPORTED_BY_SECTOR
    assert context["WEAK"]["sector_label"] == LABEL_WEAK_IN_HOT_SECTOR
    assert context["SOLO"]["sector_label"] == LABEL_STRONG_STOCK_WEAK_SECTOR
    assert context["MISSING"]["sector_label"] == LABEL_UNKNOWN_SECTOR
    assert summary["sector_leaders"] == ["LEAD"]
    assert "GOOD" in summary["sector_supported"]


def test_sector_intelligence_formatters() -> None:
    summary = {
        "available": True,
        "sector_supported": ["GOOD"],
        "sector_leaders": ["LEAD"],
        "isolated_strength": ["SOLO"],
        "weak_in_hot_sector": ["WEAK"],
    }
    report_lines = format_sector_intelligence_report_lines(summary)
    arabic_lines = format_sector_intelligence_arabic_block(summary)

    assert any("Sector-supported names: GOOD" in line for line in report_lines)
    assert "🏭 ذكاء القطاعات:" in arabic_lines
    assert any("قادة قطاعاتهم: LEAD" in line for line in arabic_lines)


def test_sector_intelligence_row_suffix_before_memory() -> None:
    enriched = enrich_section_lines_with_sector_intelligence(
        ["1. GOOD | Score 80 | Memory: IMPROVING | Seen 3x"],
        {
            "GOOD": {
                "sector_label": LABEL_SUPPORTED_BY_SECTOR,
                "relative_to_sector_pct": 1.7,
            }
        },
    )

    assert "Sector: SUPPORTED_BY_SECTOR | +1.7% vs sector | Memory:" in enriched[0]


def test_symbol_sector_intelligence_arabic_lines() -> None:
    lines = format_symbol_sector_intelligence_arabic_lines(
        {
            "sector": "Real Estate",
            "sector_label": LABEL_SUPPORTED_BY_SECTOR,
            "relative_to_sector_pct": 1.7,
            "sector_reasons": ["Hot sector supports the symbol setup"],
        }
    )

    assert any("القطاع: Real Estate" in line for line in lines)
    assert any("علاقة السهم بالقطاع: SUPPORTED_BY_SECTOR" in line for line in lines)
    assert any("أقوى من متوسط القطاع" in line for line in lines)
