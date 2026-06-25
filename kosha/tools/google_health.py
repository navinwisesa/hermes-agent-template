"""
kosha/tools/google_health.py

Fetches all available health data from the Google Health API v4
for a given user, keyed by Discord user ID.

Endpoint: https://health.googleapis.com/v4/
Docs:     https://developers.google.com/health/reference/rest

Data types fetched (all available for Kosha strain scoring):
  - steps                      → activity / sedentary detection
  - sleep                      → recovery quality
  - heart-rate                 → stress proxy
  - daily-resting-heart-rate   → baseline cardiovascular load
  - daily-heart-rate-variability → nervous system recovery signal
  - daily-oxygen-saturation    → respiratory wellness
  - sedentary-period           → prolonged desk time detection
  - active-zone-minutes        → movement intensity
  - total-calories             → energy expenditure

Uses :reconcile for all types so multi-device users (e.g. Pixel Watch + Fitbit)
get a single clean stream with no duplicate points.
Uses :dailyRollUp for aggregate daily summaries (strain scoring input).
"""

import httpx
from datetime import date, timedelta
from typing import Any

BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"

# Data types and whether they support dailyRollUp
# (list-only types still get :reconcile for raw points)
HEALTH_DATA_TYPES = {
    "steps":                        {"daily_rollup": True},
    "sleep":                        {"daily_rollup": False},
    "heart-rate":                   {"daily_rollup": False},
    "daily-resting-heart-rate":     {"daily_rollup": True},
    "daily-heart-rate-variability": {"daily_rollup": True},
    "daily-oxygen-saturation":      {"daily_rollup": True},
    "sedentary-period":             {"daily_rollup": True},
    "active-zone-minutes":          {"daily_rollup": True},
    "total-calories":               {"daily_rollup": True},
}


def _build_daily_rollup_body(target_date: date) -> dict:
    """Build the request body for a :dailyRollUp POST."""
    return {
        "range": {
            "start": {
                "date": {"year": target_date.year, "month": target_date.month, "day": target_date.day},
                "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
            },
            "end": {
                "date": {"year": target_date.year, "month": target_date.month, "day": target_date.day},
                "time": {"hours": 23, "minutes": 59, "seconds": 59, "nanos": 0},
            },
        },
        "windowSizeDays": 1,
    }


def _build_date_filter(target_date: date) -> str:
    """Build a civil time filter string for :reconcile GET requests."""
    next_day = target_date + timedelta(days=1)
    return (
        f'{{"dataType"}}.interval.civil_start_time >= "{target_date.isoformat()}" '
        f'AND {{"dataType"}}.interval.civil_start_time < "{next_day.isoformat()}"'
    )


async def fetch_health_snapshot(
    access_token: str,
    target_date: date | None = None,
) -> dict[str, Any]:
    """
    Fetch all available Google Health data for a single day.

    Args:
        access_token: Valid OAuth 2.0 bearer token for the user.
                      Retrieve from your DB using the Discord user ID
                      before calling this function.
        target_date:  Date to fetch data for. Defaults to today.

    Returns:
        Dict keyed by data type name. Each value is either:
          - {"rollup": [...]}  for daily aggregate types
          - {"points": [...]}  for raw point types (sleep, heart-rate)
          - {"error": str}     if the fetch failed (user may not have this data)

    Example:
        token = db.get_google_token(discord_user_id)
        snapshot = await fetch_health_snapshot(token)
        steps_today = snapshot["steps"]["rollup"][0]["steps"]["countSum"]
    """
    if target_date is None:
        target_date = date.today()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    results: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for data_type, config in HEALTH_DATA_TYPES.items():

            if config["daily_rollup"]:
                # POST :dailyRollUp — returns a single aggregated value for the day
                url = f"{BASE_URL}/{data_type}/dataPoints:dailyRollUp"
                try:
                    resp = await client.post(
                        url,
                        headers=headers,
                        json=_build_daily_rollup_body(target_date),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    results[data_type] = {"rollup": data.get("rollupDataPoints", [])}
                except httpx.HTTPStatusError as e:
                    # 404 = user has no data of this type; 403 = scope not granted
                    results[data_type] = {"error": f"HTTP {e.response.status_code}"}
                except Exception as e:
                    results[data_type] = {"error": str(e)}

            else:
                # GET :reconcile — returns merged raw points across all user devices
                url = f"{BASE_URL}/{data_type}/dataPoints:reconcile"
                params = {
                    "filter": (
                        f'interval.civil_start_time >= "{target_date.isoformat()}" '
                        f'AND interval.civil_start_time < "{(target_date + timedelta(days=1)).isoformat()}"'
                    )
                }
                try:
                    resp = await client.get(url, headers=headers, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    results[data_type] = {"points": data.get("dataPoints", [])}
                except httpx.HTTPStatusError as e:
                    results[data_type] = {"error": f"HTTP {e.response.status_code}"}
                except Exception as e:
                    results[data_type] = {"error": str(e)}

    return results


def parse_snapshot_for_strain(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Extract clean scalar values from a raw health snapshot
    for use in Kosha's strain score calculation.

    Returns a flat dict of wellness signals, with None for any
    data type the user doesn't have. The strain scorer should
    treat None as "no signal" and weight accordingly.
    """

    def safe_get(data_type: str, *keys):
        """Safely walk nested keys; return None on any miss or error."""
        node = snapshot.get(data_type, {})
        if "error" in node:
            return None
        items = node.get("rollup") or node.get("points") or []
        if not items:
            return None
        try:
            result = items[0]
            for k in keys:
                result = result[k]
            return result
        except (KeyError, IndexError, TypeError):
            return None

    return {
        # Movement
        "steps_today":          safe_get("steps", "steps", "countSum"),
        "active_zone_minutes":  safe_get("active-zone-minutes", "activeZoneMinutes", "totalMinutes"),
        "calories_burned":      safe_get("total-calories", "totalCalories", "kilocalories"),

        # Sedentary signal (key input for burnout detection)
        "sedentary_minutes":    safe_get("sedentary-period", "sedentaryPeriod", "durationMs"),

        # Cardiovascular / stress proxies
        "resting_heart_rate":   safe_get("daily-resting-heart-rate", "dailyRestingHeartRate", "beatsPerMinute"),
        "hrv_rmssd":            safe_get("daily-heart-rate-variability", "dailyHeartRateVariability", "rmssd"),

        # Recovery
        "spo2_avg":             safe_get("daily-oxygen-saturation", "dailyOxygenSaturation", "avgPercent"),

        # Sleep (raw points — extract total duration manually)
        "sleep_points":         (snapshot.get("sleep", {}).get("points") or []),
        "heart_rate_points":    (snapshot.get("heart-rate", {}).get("points") or []),
    }
