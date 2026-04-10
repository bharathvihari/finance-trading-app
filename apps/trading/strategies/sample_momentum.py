from dataclasses import dataclass


@dataclass
class MomentumConfig:
    symbol: str = "SPY"
    fast_window: int = 20
    slow_window: int = 50


class SampleMomentumStrategy:
    def __init__(self, config: MomentumConfig):
        self.config = config

    def on_bar(self, close_prices: list[float]) -> str:
        if len(close_prices) < self.config.slow_window:
            return "hold"

        fast = sum(close_prices[-self.config.fast_window :]) / self.config.fast_window
        slow = sum(close_prices[-self.config.slow_window :]) / self.config.slow_window

        if fast > slow:
            return "buy_or_hold"
        if fast < slow:
            return "sell_or_reduce"
        return "hold"
