"""Manual KuCoin trading — never auto-executed from the scanner."""

from .kucoin_manual import KuCoinManualTrader, TradeRequest, TradeResult

__all__ = ["KuCoinManualTrader", "TradeRequest", "TradeResult"]
