"""Tests for Market Memory V1."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.cloud_state_store import MARKET_MEMORY_KEY
from core.market_memory import (
    LABEL_FADING,
    LABEL_IMPROVING,
    LABEL_NEW,
    LABEL_PERSISTENT,
    STATUS_BLOCKED,
    STATUS_CANDIDATE,
    STATUS_SIGNAL,
    STATUS_WATCH,
    SymbolObservation,
    enrich_section_lines_with_memory,
    format_market_memory_arabic_block,
    format_symbol_memory_arabic_lines,
    load_market_memory_state,
    process_market_memory,
    update_market_memory_state,
)


def test_market_memory_new_symbol_label() -> None:
    state, context, summary = update_market_memory_state(
        {"symbols": {}},
        [SymbolObservation("ELKA", status=STATUS_CANDIDATE, score=80, rank=3)],
        report_date=date(2026, 7, 1),
    )

    assert state["symbols"]["ELKA"]["last_memory_label"] == LABEL_NEW
    assert context["ELKA"]["memory_label"] == LABEL_NEW
    assert summary["new"] == ["ELKA"]


def test_market_memory_status_promotion_is_improving() -> None:
    previous = {
        "symbols": {
            "ELKA": {
                "symbol": "ELKA",
                "first_seen_date": "2026-07-01",
                "last_seen_date": "2026-07-01",
                "appearances_total": 1,
                "recent_appearances": 1,
                "last_status": STATUS_BLOCKED,
                "last_score": 55,
                "last_rank": 7,
            }
        }
    }

    _state, context, summary = update_market_memory_state(
        previous,
        [SymbolObservation("ELKA", status=STATUS_WATCH, score=66, rank=5)],
        report_date=date(2026, 7, 2),
    )

    assert context["ELKA"]["memory_label"] == LABEL_IMPROVING
    assert context["ELKA"]["previous_status"] == STATUS_BLOCKED
    assert context["ELKA"]["score_delta"] == 11
    assert summary["improving"] == ["ELKA"]


def test_market_memory_status_demotion_is_fading() -> None:
    previous = {
        "symbols": {
            "ELKA": {
                "symbol": "ELKA",
                "first_seen_date": "2026-07-01",
                "last_seen_date": "2026-07-01",
                "appearances_total": 1,
                "recent_appearances": 1,
                "last_status": STATUS_SIGNAL,
                "last_score": 90,
                "last_rank": 1,
            }
        }
    }

    _state, context, summary = update_market_memory_state(
        previous,
        [SymbolObservation("ELKA", status=STATUS_WATCH, score=72, rank=6)],
        report_date=date(2026, 7, 2),
    )

    assert context["ELKA"]["memory_label"] == LABEL_FADING
    assert summary["fading"] == ["ELKA"]


def test_market_memory_repeated_symbol_is_persistent() -> None:
    previous = {
        "symbols": {
            "ELKA": {
                "symbol": "ELKA",
                "first_seen_date": "2026-07-01",
                "last_seen_date": "2026-07-02",
                "appearances_total": 2,
                "recent_appearances": 2,
                "last_status": STATUS_CANDIDATE,
                "last_score": 80,
                "last_rank": 3,
            }
        }
    }

    _state, context, summary = update_market_memory_state(
        previous,
        [SymbolObservation("ELKA", status=STATUS_CANDIDATE, score=81, rank=3)],
        report_date=date(2026, 7, 3),
    )

    assert context["ELKA"]["memory_label"] == LABEL_PERSISTENT
    assert summary["persistent"] == ["ELKA"]


def test_enrich_section_lines_with_memory() -> None:
    lines = ["1. ELKA | Score 80 | Change +2.00%", "   Reasons: x"]
    enriched = enrich_section_lines_with_memory(
        lines,
        {"ELKA": {"memory_label": LABEL_IMPROVING, "appearances_total": 3}},
    )

    assert "Memory: IMPROVING" in enriched[0]
    assert "Seen 3x" in enriched[0]
    assert enriched[1] == lines[1]


def test_market_memory_arabic_formatters() -> None:
    block = format_market_memory_arabic_block(
        {
            "available": True,
            "new": ["ELKA"],
            "improving": ["COMI"],
            "persistent": ["HRHO"],
            "fading": ["ABUK"],
            "weakening": [],
        }
    )
    assert "🧠 ذاكرة السوق:" in block
    assert any("بيتحسن: COMI" in line for line in block)

    lines = format_symbol_memory_arabic_lines(
        {
            "memory_label": LABEL_IMPROVING,
            "appearances_total": 3,
            "recent_appearances": 2,
            "previous_score": 65,
            "last_score": 80,
            "previous_status": STATUS_WATCH,
            "last_status": STATUS_CANDIDATE,
        }
    )
    assert any("ذاكرة السهم: IMPROVING" in line for line in lines)
    assert any("65" in line and "80" in line for line in lines)


def test_corrupt_memory_file_starts_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_path = tmp_path / "storage" / "market_memory.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(
        "core.cloud_state_store._STATE_KEY_TO_LOCAL_PATH",
        {MARKET_MEMORY_KEY: memory_path},
    )

    state = load_market_memory_state()

    assert state["symbols"] == {}


def test_process_market_memory_writes_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_path = tmp_path / "storage" / "market_memory.json"
    monkeypatch.setattr(
        "core.cloud_state_store._STATE_KEY_TO_LOCAL_PATH",
        {MARKET_MEMORY_KEY: memory_path},
    )

    available, context, summary = process_market_memory(
        [SymbolObservation("ELKA", status=STATUS_CANDIDATE, score=80)],
        report_date=date(2026, 7, 1),
    )

    assert available is True
    assert context["ELKA"]["memory_label"] == LABEL_NEW
    assert summary["new"] == ["ELKA"]
    assert json.loads(memory_path.read_text(encoding="utf-8"))["symbols"]["ELKA"]
