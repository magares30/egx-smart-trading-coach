"""Tests for Smarter Confidence Score V2."""

from __future__ import annotations

from core.confidence_score import (
    ConfidenceInput,
    build_confidence_v2,
    build_confidence_v2_context,
    confidence_label_from_score,
    enrich_section_lines_with_confidence_v2,
    format_confidence_v2_arabic_block,
    format_confidence_v2_report_lines,
    format_symbol_confidence_v2_arabic_lines,
)


def test_confidence_label_thresholds() -> None:
    assert confidence_label_from_score(85) == "STRONG"
    assert confidence_label_from_score(70) == "GOOD"
    assert confidence_label_from_score(55) == "MIXED"
    assert confidence_label_from_score(40) == "WEAK"
    assert confidence_label_from_score(39) == "WAIT"


def test_confidence_v2_combines_supportive_components() -> None:
    result = build_confidence_v2(
        ConfidenceInput(
            symbol="ELKA",
            base_score=75,
            technical_status="STRONG",
            technical_score=12,
            talib_status="STRONG",
            talib_available=True,
            market_mood="BULLISH",
            memory_label="IMPROVING",
            sector_status="HOT",
            risk_reward=2.0,
            fundamental_status="OK",
            volume_ratio=1.8,
        )
    )

    assert result["symbol"] == "ELKA"
    assert result["confidence_score_v2"] == 100
    assert result["confidence_label_v2"] == "STRONG"
    assert result["confidence_components_v2"]["memory"] > 0
    assert result["confidence_reasons_v2"]


def test_confidence_v2_closed_market_keeps_analysis_but_adds_risk() -> None:
    result = build_confidence_v2(
        ConfidenceInput(
            symbol="ELKA",
            base_score=80,
            technical_status="OK",
            market_mood="NEUTRAL",
            market_closed=True,
            stale_prices=True,
        )
    )

    assert result["confidence_score_v2"] >= 55
    assert any("market closed" in risk for risk in result["confidence_risks_v2"])
    assert result["confidence_components_v2"]["session"] < 0


def test_confidence_v2_uses_sector_intelligence_label() -> None:
    supported = build_confidence_v2(
        ConfidenceInput(
            symbol="ELKA",
            base_score=60,
            sector_status="HOT",
            sector_intelligence_label="SUPPORTED_BY_SECTOR",
        )
    )
    risky = build_confidence_v2(
        ConfidenceInput(
            symbol="WEAK",
            base_score=60,
            sector_status="HOT",
            sector_intelligence_label="WEAK_IN_HOT_SECTOR",
        )
    )

    assert supported["confidence_components_v2"]["sector"] > 0
    assert risky["confidence_components_v2"]["sector"] > 0
    assert supported["confidence_score_v2"] > risky["confidence_score_v2"]


def test_confidence_v2_context_skips_bad_symbol_safely() -> None:
    context, summary, available = build_confidence_v2_context(
        [ConfidenceInput(symbol="ELKA", base_score=80)]
    )

    assert available is True
    assert context["ELKA"]["confidence_label_v2"] in {"GOOD", "MIXED", "STRONG"}
    assert summary["available"] is True


def test_confidence_report_and_arabic_formatters() -> None:
    summary = {
        "available": True,
        "strong": ["ELKA"],
        "good": ["COMI"],
        "mixed": ["HRHO"],
        "wait": ["ABUK"],
        "main_risks": ["market closed; review next session"],
        "top_reason": "Market Memory shows improvement",
    }
    report_lines = format_confidence_v2_report_lines(summary)
    arabic_lines = format_confidence_v2_arabic_block(summary)

    assert any("Strong confidence: ELKA" in line for line in report_lines)
    assert "🧠 الثقة الذكية:" in arabic_lines
    assert any("قوي: ELKA" in line for line in arabic_lines)


def test_symbol_confidence_arabic_lines() -> None:
    lines = format_symbol_confidence_v2_arabic_lines(
        {
            "confidence_label_v2": "GOOD",
            "confidence_score_v2": 76,
            "confidence_reasons_v2": ["Technical supportive"],
            "confidence_risks_v2": ["market closed; review next session"],
            "confidence_components_v2": {"technical": 8, "session": -8},
        }
    )

    assert any("الثقة الذكية: GOOD 76" in line for line in lines)
    assert any("أسباب الثقة" in line for line in lines)
    assert any("مخاطر الثقة" in line for line in lines)


def test_enrich_section_lines_with_confidence_before_memory() -> None:
    lines = ["1. ELKA | Score 80 | Memory: IMPROVING | Seen 3x"]
    enriched = enrich_section_lines_with_confidence_v2(
        lines,
        {"ELKA": {"confidence_label_v2": "GOOD", "confidence_score_v2": 76}},
    )

    assert "Confidence V2: GOOD 76 | Memory: IMPROVING" in enriched[0]
