---
name: kosha_health
version: 1.0.0
description: >
  Fetches a user's physical wellness snapshot from the Google Health API
  (steps, sleep, heart rate, HRV, SpO2, sedentary time, calories, active
  zone minutes) for use in Kosha's strain score calculation.
  Always call this alongside fetch_calendar_context before computing strain.
triggers:
  - "check health"
  - "physical snapshot"
  - "wellness data"
  - "strain score"
  - "how is the user physically"
  - "sedentary today"
  - "sleep last night"
requires:
  - discord_user_id       # mapped to Google OAuth token in DB
  - google_oauth_token    # retrieved from DB before calling
---

# kosha_health skill

## Purpose
Retrieve all available physical health signals for a Kosha user from the
Google Health API v4. This is one half of the strain score input — the other
half is the calendar/email cognitive load from `kosha_calendar` skill.

## When to use
- Before computing any strain score
- When a user asks how they're doing physically
- When proactive check-in logic fires and needs physical context
- When scheduling recovery blocks or break suggestions

## How to call

```python
from kosha.tools.google_health import fetch_health_snapshot, parse_snapshot_for_strain
from kosha.db import get_google_token  # your existing DB lookup

async def get_physical_context(discord_user_id: str, target_date=None):
    token = get_google_token(discord_user_id)
    if not token:
        return None  # user hasn't connected Google Health — degrade gracefully

    raw = await fetch_health_snapshot(access_token=token, target_date=target_date)
    return parse_snapshot_for_strain(raw)
```

## Output shape

```python
{
    # Movement
    "steps_today":         "8432",   # str countSum or None
    "active_zone_minutes": 34,       # int or None
    "calories_burned":     2100.5,   # float kcal or None

    # Sedentary signal — key burnout indicator
    "sedentary_minutes":   18000000, # duration in ms (divide by 60000 for mins)

    # Cardiovascular stress proxies
    "resting_heart_rate":  62,       # bpm or None
    "hrv_rmssd":           38.2,     # ms; lower = more stressed

    # Recovery
    "spo2_avg":            97.1,     # % or None

    # Raw point arrays for deeper analysis
    "sleep_points":        [...],    # list of sleep stage intervals
    "heart_rate_points":   [...],    # list of intraday HR readings
}
```

## Graceful degradation
Every field can be `None` if:
- The user has no device synced to Google Health
- The OAuth scope wasn't granted for that data type
- The user simply didn't wear a device today

The strain scorer must handle `None` values by zeroing that signal's
contribution — never block on missing health data.

## Sedentary minutes conversion
```python
sedentary_mins = (parsed["sedentary_minutes"] or 0) / 60_000
```

## Sleep duration from raw points
```python
def total_sleep_minutes(sleep_points: list) -> float:
    total_ms = 0
    for point in sleep_points:
        start = point["sleep"]["interval"]["startTime"]
        end   = point["sleep"]["interval"]["endTime"]
        # parse ISO timestamps and diff
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        total_ms += (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).seconds
    return total_ms / 60
```

## Notes
- Uses `:reconcile` for sleep and heart-rate (merges multi-device data automatically)
- Uses `:dailyRollUp` for all aggregate types (single clean value per day)
- Timeout is 15s; catches HTTP 404 (no data) and 403 (scope missing) gracefully
- Do NOT cache tokens in memory — always fetch from DB to respect revocation
