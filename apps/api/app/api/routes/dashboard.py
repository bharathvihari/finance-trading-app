from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardQuery(BaseModel):
    ticker: str = Field(default="SPY")
    timeframe: str = Field(default="5y")
    metric: str = Field(default="cagr")


@router.post("/query")
async def query_dashboard(
    payload: DashboardQuery,
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    # Placeholder for analytics engine integration (Parquet + DB reads)
    return {
        "ticker": payload.ticker,
        "timeframe": payload.timeframe,
        "metric": payload.metric,
        "data": [],
        "message": "Hook this to analytics service and historical store.",
    }
