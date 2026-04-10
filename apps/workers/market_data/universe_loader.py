from .config import JobConfig
from .models import Instrument, SymbolUniverse


def load_universe(config: JobConfig) -> SymbolUniverse:
    """Build deterministic priority-first universe with dedup + normalization.

    Ordering rules:
    1. Priority instruments first (equity + index), then non-priority.
    2. Preserve configured exchange iteration order.
    3. Preserve symbol order within each configured list.
    4. De-duplicate by (exchange, asset_class, symbol).
       - If duplicate appears again with priority=True, promote existing row.
    """
    buckets: dict[bool, list[Instrument]] = {True: [], False: []}
    index_map: dict[tuple[str, str, str], tuple[bool, int]] = {}

    def _normalized_symbol(symbol: str) -> str:
        return symbol.strip().upper()

    def _append(exchange: str, asset_class: str, symbol: str, priority: bool) -> None:
        clean = _normalized_symbol(symbol)
        if not clean:
            return

        key = (exchange, asset_class, clean)
        existing = index_map.get(key)
        if existing is None:
            idx = len(buckets[priority])
            buckets[priority].append(
                Instrument(symbol=clean, exchange=exchange, asset_class=asset_class, priority=priority)
            )
            index_map[key] = (priority, idx)
            return

        was_priority, idx = existing
        if priority and not was_priority:
            current = buckets[False][idx]
            buckets[False][idx] = Instrument(
                symbol=current.symbol,
                exchange=current.exchange,
                asset_class=current.asset_class,
                priority=True,
            )
            promoted = buckets[False].pop(idx)
            # Rebuild non-priority indices after pop.
            for i, inst in enumerate(buckets[False]):
                index_map[(inst.exchange, inst.asset_class, inst.symbol)] = (False, i)
            buckets[True].append(promoted)
            index_map[key] = (True, len(buckets[True]) - 1)

    for exchange, exchange_cfg in config.universe.exchanges.items():
        for symbol in exchange_cfg.priority_symbols:
            _append(exchange=exchange, asset_class="equity", symbol=symbol, priority=True)
        for symbol in exchange_cfg.priority_indices:
            _append(exchange=exchange, asset_class="index", symbol=symbol, priority=True)
        for symbol in exchange_cfg.symbols:
            _append(exchange=exchange, asset_class="equity", symbol=symbol, priority=False)
        for symbol in exchange_cfg.indices:
            _append(exchange=exchange, asset_class="index", symbol=symbol, priority=False)

    return SymbolUniverse(instruments=[*buckets[True], *buckets[False]])
