from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Frequency(str, Enum):
    DAILY = "daily"


class SliceStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


AssetClass = Literal["equity", "index"]


@dataclass(frozen=True)
class Instrument:
    symbol: str
    exchange: str
    asset_class: AssetClass
    priority: bool = False


@dataclass
class SymbolUniverse:
    instruments: list[Instrument] = field(default_factory=list)

    def prioritized(self) -> list[Instrument]:
        return sorted(self.instruments, key=lambda item: (not item.priority, item.exchange, item.symbol))
