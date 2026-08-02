"""Real weather metrics feeding the pest model (Open-Meteo).

Extracted from the analysis endpoint so non-endpoint callers (e.g. task_generator's
photo-priority sampling, Active Learning Fase 0 — see docs/ACTIVE_LEARNING.md §1) can
reuse the same weather read instead of duplicating the Open-Meteo call.
"""

from __future__ import annotations

import httpx

_FORECAST_DAYS = 4  # today + 3 days ahead — enough runway to say "the window opens in 2 days"


async def fetch_pest_weather(lat: float, lon: float) -> dict:
    """Weather metrics for the pest model: temp, RH, leaf-wetness hours, daily means,
    plus a same-shape 3-day forecast (Open-Meteo's own model) for the trend view.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,precipitation",
                    "hourly": "relative_humidity_2m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,relative_humidity_2m_mean",
                    "past_days": 30,
                    "forecast_days": _FORECAST_DAYS,
                    "timezone": "auto",
                },
            )
        if r.status_code != 200:
            return {}
        d = r.json()
        cur = d.get("current", {})
        rh = d.get("hourly", {}).get("relative_humidity_2m", []) or []
        # "Today" boundary: past_days*24 hourly points precede it.
        today_start = 30 * 24
        wet_hours = sum(1 for v in rh[today_start - 72 : today_start] if v is not None and v >= 90)
        daily = d.get("daily", {})
        tmax_all = daily.get("temperature_2m_max", []) or []
        tmin_all = daily.get("temperature_2m_min", []) or []
        rhmean_all = daily.get("relative_humidity_2m_mean", []) or []
        # Degree-days to date must stop at "today" — the forecast tail is handled
        # separately by forecast_pest() so future days aren't double-counted as past.
        upto_today = len(tmax_all) - _FORECAST_DAYS + 1
        means = [
            (a + b) / 2
            for a, b in zip(tmax_all[:upto_today], tmin_all[:upto_today], strict=False)
            if a is not None and b is not None
        ]
        rain = sum(
            (daily.get("precipitation_sum", []) or [0])[-(_FORECAST_DAYS + 2) : -_FORECAST_DAYS] or [0]
        )

        # Last _FORECAST_DAYS daily entries = today .. today+3.
        fc_tmax, fc_tmin, fc_rh = (
            tmax_all[-_FORECAST_DAYS:],
            tmin_all[-_FORECAST_DAYS:],
            rhmean_all[-_FORECAST_DAYS:],
        )
        forecast_days = []
        for i in range(len(fc_tmax)):
            t = (fc_tmax[i] + fc_tmin[i]) / 2 if fc_tmax[i] is not None and fc_tmin[i] is not None else None
            rh_d = fc_rh[i]
            # Hourly-derived wet_hours only exists for today; future days use a
            # coarse proxy from the daily mean humidity (documented, not precise).
            wh = (
                wet_hours
                if i == 0
                else (
                    6 if (rh_d is not None and rh_d >= 90) else 3 if (rh_d is not None and rh_d >= 80) else 0
                )
            )
            forecast_days.append({"offset": i, "temp": t, "humidity": rh_d, "wet_hours": wh})

        return {
            "temp": cur.get("temperature_2m"),
            "humidity": cur.get("relative_humidity_2m"),
            "wet_hours": wet_hours,
            "recent_rain_mm": rain,
            "daily_means": means,
            "forecast_days": forecast_days,
        }
    except Exception:
        return {}


def degree_days_to_date(daily_means: list[float], base: float) -> float:
    """Accumulated degree-days above *base* from a list of daily mean temperatures."""
    return sum(max(0.0, m - base) for m in daily_means)
