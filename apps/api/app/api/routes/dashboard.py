from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardQuery(BaseModel):
    ticker: str = Field(default="SPY")
    timeframe: str = Field(default="5y")
    metric: str = Field(default="cagr")


@router.post("/query")
async def query_dashboard(payload: DashboardQuery) -> dict:
    # Placeholder for analytics engine integration (Parquet + DB reads)
    return {
        "ticker": payload.ticker,
        "timeframe": payload.timeframe,
        "metric": payload.metric,
        "data": [],
        "message": "Hook this to analytics service and historical store.",
    }
