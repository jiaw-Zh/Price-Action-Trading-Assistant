"""Page routes for server-rendered HTML."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Main dashboard page."""
    from pa_assistant.web.app import templates

    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/liquidity", response_class=HTMLResponse)
async def liquidity(request: Request) -> HTMLResponse:
    """Liquidity analysis page."""
    from pa_assistant.web.app import templates

    return templates.TemplateResponse("liquidity.html", {"request": request})


@router.get("/backtest", response_class=HTMLResponse)
async def backtest(request: Request) -> HTMLResponse:
    """Backtest replay page."""
    from pa_assistant.web.app import templates

    return templates.TemplateResponse("backtest.html", {"request": request})
