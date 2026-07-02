"""Risk management for position sizing and trade approval."""

from config import settings
from core.models import RiskDecision, SignalType, TradeSignal


class RiskManager:
    """Evaluates trade signals against risk rules and calculates position size."""

    MIN_RISK_REWARD_RATIO: float = 2.0

    def evaluate(self, signal: TradeSignal, equity: float) -> RiskDecision:
        """Return approval or rejection with calculated share quantity."""
        rejection_reasons: list[str] = []

        if signal.signal_type == SignalType.BLOCKED:
            rejection_reasons.append("Signal is blocked")

        if signal.signal_type == SignalType.BUY_SETUP:
            if signal.stop_loss >= signal.entry_price:
                rejection_reasons.append(
                    f"Invalid stop loss: {signal.stop_loss} must be below "
                    f"entry {signal.entry_price}"
                )

            risk_per_share = signal.entry_price - signal.stop_loss
            reward_per_share = signal.take_profit - signal.entry_price

            if risk_per_share > 0:
                risk_reward = reward_per_share / risk_per_share
                if risk_reward < self.MIN_RISK_REWARD_RATIO:
                    rejection_reasons.append(
                        f"Risk/reward {risk_reward:.2f}:1 is below minimum "
                        f"{self.MIN_RISK_REWARD_RATIO:.1f}:1"
                    )
            else:
                rejection_reasons.append("Risk per share must be positive")

        if rejection_reasons:
            return RiskDecision(
                approved=False,
                quantity=0,
                message="Trade rejected",
                rejection_reasons=rejection_reasons,
            )

        risk_amount = equity * (settings.RISK_PER_TRADE_PERCENT / 100)
        risk_per_share = abs(signal.entry_price - signal.stop_loss)
        quantity = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0

        if quantity < 1:
            return RiskDecision(
                approved=False,
                quantity=0,
                message="Trade rejected",
                rejection_reasons=["Calculated quantity is zero — position too small"],
            )

        reward_per_share = abs(signal.take_profit - signal.entry_price)
        risk_reward = reward_per_share / risk_per_share

        return RiskDecision(
            approved=True,
            quantity=quantity,
            message=(
                f"Approved: {quantity} shares | Risk: {risk_amount:,.2f} "
                f"{settings.BASE_CURRENCY} | R:R: 1:{risk_reward:.1f}"
            ),
        )
