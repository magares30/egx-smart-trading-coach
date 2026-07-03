"""Tests for Cloud Run paper portfolio bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from config import settings
from core.cloud_paper_portfolio_bootstrap import (
    bootstrap_cloud_paper_portfolio,
    run_egx_bootstrap_cloud_paper_portfolio,
)
from core.cloud_state_store import (
    EGX_STATE_GCS_BUCKET_ENV,
    GcsStateStore,
    PORTFOLIO_STATE_KEY,
    TRADES_KEY,
)
from core.portfolio import VirtualPortfolio
from core.trade_journal import TradeJournal
from main import parse_args
from tests.test_cloud_state_store import FakeBucket


def test_parse_args_bootstrap_cloud_paper_portfolio() -> None:
    args = parse_args(["--egx-bootstrap-cloud-paper-portfolio"])
    assert args.egx_bootstrap_cloud_paper_portfolio is True
    assert args.force_bootstrap_paper_portfolio is False

    forced = parse_args(
        [
            "--egx-bootstrap-cloud-paper-portfolio",
            "--force-bootstrap-paper-portfolio",
        ]
    )
    assert forced.force_bootstrap_paper_portfolio is True


def test_bootstrap_creates_local_files(tmp_path: Path) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"

    result = bootstrap_cloud_paper_portfolio(
        portfolio_path=portfolio_path,
        trades_path=trades_path,
    )

    assert portfolio_path.is_file()
    assert trades_path.is_file()
    assert result.portfolio_status == "created fresh paper portfolio"
    assert result.journal_status == "created empty trade journal"
    assert result.open_positions == 0
    assert result.closed_trades == 0
    assert result.initial_capital == settings.INITIAL_CAPITAL_EGP

    portfolio_data = json.loads(portfolio_path.read_text(encoding="utf-8"))
    trades_data = json.loads(trades_path.read_text(encoding="utf-8"))
    assert portfolio_data["positions"] == {}
    assert portfolio_data["trades"] == {}
    assert trades_data == []


def test_bootstrap_does_not_overwrite_existing_local_files(tmp_path: Path) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"
    portfolio_path.parent.mkdir(parents=True)
    portfolio_path.write_text(
        json.dumps(
            {
                "cash": 123.0,
                "initial_capital": 123.0,
                "realized_pnl": 0.0,
                "positions": {},
                "trades": {},
            }
        ),
        encoding="utf-8",
    )
    trades_path.write_text("[]", encoding="utf-8")

    result = bootstrap_cloud_paper_portfolio(
        portfolio_path=portfolio_path,
        trades_path=trades_path,
    )

    assert result.portfolio_status == "already exists (not overwritten)"
    assert result.journal_status == "already exists (not overwritten)"
    assert json.loads(portfolio_path.read_text(encoding="utf-8"))["cash"] == 123.0


def test_bootstrap_force_overwrite_resets_files(tmp_path: Path) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"
    portfolio_path.parent.mkdir(parents=True)
    portfolio_path.write_text(
        json.dumps(
            {
                "cash": 123.0,
                "initial_capital": 123.0,
                "realized_pnl": 0.0,
                "positions": {},
                "trades": {},
            }
        ),
        encoding="utf-8",
    )
    trades_path.write_text("[]", encoding="utf-8")

    result = bootstrap_cloud_paper_portfolio(
        force=True,
        portfolio_path=portfolio_path,
        trades_path=trades_path,
    )

    assert result.portfolio_status == "overwritten with fresh paper portfolio"
    assert result.journal_status == "overwritten with empty trade journal"
    assert json.loads(trades_path.read_text(encoding="utf-8")) == []
    portfolio = VirtualPortfolio(state_path=portfolio_path)
    assert portfolio.cash == settings.INITIAL_CAPITAL_EGP
    assert portfolio.positions == {}


def test_bootstrap_skips_when_gcs_objects_exist_without_force(tmp_path: Path) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"
    bucket = FakeBucket(
        {
            PORTFOLIO_STATE_KEY: '{"cash": 999.0}',
            TRADES_KEY: "[]",
        }
    )
    store = GcsStateStore("egx-state-test", bucket=bucket)

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        result = bootstrap_cloud_paper_portfolio(
            portfolio_path=portfolio_path,
            trades_path=trades_path,
            state_store=store,
        )

    assert result.portfolio_status == "already exists (not overwritten)"
    assert result.journal_status == "already exists (not overwritten)"
    assert not portfolio_path.exists()
    assert not trades_path.exists()


def test_bootstrap_uploads_to_gcs_when_bucket_configured(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"
    bucket = FakeBucket()
    store = GcsStateStore("egx-state-test", bucket=bucket)

    with patch.dict(os.environ, {EGX_STATE_GCS_BUCKET_ENV: "egx-state-test"}, clear=False):
        with patch("core.cloud_state_store.settings.PORTFOLIO_STATE_PATH", portfolio_path):
            with patch("core.cloud_state_store.settings.TRADES_PATH", trades_path):
                with patch("core.cloud_state_store.get_state_store", return_value=store):
                    with caplog.at_level("INFO"):
                        result = bootstrap_cloud_paper_portfolio(
                            portfolio_path=portfolio_path,
                            trades_path=trades_path,
                            state_store=store,
                        )

    assert result.gcs_upload == "uploaded portfolio/journal to GCS"
    assert PORTFOLIO_STATE_KEY in bucket._objects
    assert TRADES_KEY in bucket._objects
    assert "1234567890:AA" not in caplog.text


def test_bootstrap_creates_no_real_trades(tmp_path: Path) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"

    bootstrap_cloud_paper_portfolio(
        portfolio_path=portfolio_path,
        trades_path=trades_path,
    )

    portfolio = VirtualPortfolio(state_path=portfolio_path)
    journal = TradeJournal(journal_path=trades_path)
    assert portfolio.get_open_trades() == []
    assert journal.trades == []


def test_run_egx_bootstrap_cloud_paper_portfolio_prints_safe_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    portfolio_path = tmp_path / "storage" / "portfolio_state.json"
    trades_path = tmp_path / "storage" / "trades.json"
    monkeypatch.setattr(settings, "PORTFOLIO_STATE_PATH", portfolio_path)
    monkeypatch.setattr(settings, "TRADES_PATH", trades_path)

    exit_code = run_egx_bootstrap_cloud_paper_portfolio()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "created fresh paper portfolio" in output
    assert "created empty trade journal" in output
    assert "No secrets logged" in output
    assert "TELEGRAM_BOT_TOKEN" not in output
