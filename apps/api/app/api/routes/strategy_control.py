from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/strategies", tags=["strategies"])


class StrategyCommand(BaseModel):
    strategy_name: str = Field(...)
    mode: str = Field(default="paper")
    params: dict = Field(default_factory=dict)


@router.post("/start")
async def start_strategy(payload: StrategyCommand) -> dict:
    # TODO: Wire to NautilusTrader control interface/event bus.
    return {"status": "accepted", "action": "start", "strategy": payload.strategy_name, "mode": payload.mode}


@router.post("/stop")
async def stop_strategy(payload: StrategyCommand) -> dict:
    # TODO: Wire to NautilusTrader control interface/event bus.
    return {"status": "accepted", "action": "stop", "strategy": payload.strategy_name}
