"""Top-level v1 API router — assembles all endpoint routers."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    advisory,
    analysis,
    auth,
    billing,
    chat,
    dashboard,
    fields,
    indices,
    insights,
    rasters,
    roles,
    weather,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(fields.router, prefix="/fields", tags=["fields"])
api_router.include_router(indices.router, prefix="/fields", tags=["indices"])
# Generate insight is POST /fields/{id}/insights/generate
api_router.include_router(insights.fields_router, prefix="/fields", tags=["insights"])
# Retrieve/purchase insight is GET|POST /insights/{id}[/buy]
api_router.include_router(insights.router, prefix="/insights", tags=["insights"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(weather.router, prefix="/fields", tags=["weather"])
api_router.include_router(rasters.router, prefix="/fields", tags=["rasters"])
api_router.include_router(chat.router, prefix="", tags=["chat"])
api_router.include_router(advisory.router, prefix="/fields", tags=["advisory"])
# Role-based views: /ranch, /tasks, /irrigation, /portfolio, /fields/{id}/photos
api_router.include_router(roles.router, prefix="", tags=["roles"])
# Multi-sensor analysis: /fields/{id}/analysis/{timeseries,layers,render,fuse}
api_router.include_router(analysis.router, prefix="/fields", tags=["analysis"])
# Billing: /billing/{plans,me,checkout,subscribe}
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
