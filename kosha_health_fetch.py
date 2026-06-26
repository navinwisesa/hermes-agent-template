#!/usr/bin/env python3
"""
kosha_health_fetch.py — CLI entry point for Kosha's Google Health integration.

Hermes calls this script directly, the same way it calls kosha_auth.py.
It reads the user's OAuth token from the existing google_token.json on disk,
fetches all available Google Health data, and prints a clean JSON summary.

Usage (from Hermes / system prompt):
    python /data/.hermes/skills/productivity/google-workspace/scripts/kosha_health_fetch.py

Output:
    JSON object with all available health signals, or {"error": "..."} on failure.
"""

import json
import sys
import asyncio
from pathlib import Path
from datetime import date

# --- Token loading (reuses existing Kosha token infrastructure) ---
TOKEN_PATH = Path("/data/.hermes/google_token.json")
CREDS_PATH = Path("/data/.hermes/google_client_secret.json")

def load_access_token() -> str | None:
    """Load the current access token from disk, refreshing if needed."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        import google.auth.exceptions

        if not TOKEN_PATH.exists():
            return None

        creds = Credentials.from_authorized_user_file(
            str(TOKEN_PATH),
            scopes=[
                "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
                "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
                "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            ]
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token back to disk
            TOKEN_PATH.write_text(creds.to_json())

        return creds.token

    except Exception as e:
        return None


async def main():
    # Load token
    token = load_access_token()
    if not token:
        print(json.dumps({"error": "NOT_AUTHENTICATED", "message": "No valid Google token found. Re-authenticate at /setup/api/auth/google"}))
        sys.exit(1)

    # Import and run health fetch
    try:
        sys.path.insert(0, "/app")
        from kosha.tools.google_health import fetch_health_snapshot, parse_snapshot_for_strain

        raw = await fetch_health_snapshot(access_token=token, target_date=date.today())
        parsed = parse_snapshot_for_strain(raw)

        # Convert to serializable format (remove raw point arrays for brevity)
        output = {
            "status": "OK",
            "date": date.today().isoformat(),
            "steps_today":          parsed.get("steps_today"),
            "active_zone_minutes":  parsed.get("active_zone_minutes"),
            "calories_burned":      parsed.get("calories_burned"),
            "sedentary_minutes":    round((parsed.get("sedentary_minutes") or 0) / 60000, 1),
            "resting_heart_rate":   parsed.get("resting_heart_rate"),
            "hrv_rmssd":            parsed.get("hrv_rmssd"),
            "spo2_avg":             parsed.get("spo2_avg"),
            "sleep_sessions":       len(parsed.get("sleep_points") or []),
            "heart_rate_readings":  len(parsed.get("heart_rate_points") or []),
        }

        print(json.dumps(output, indent=2))

    except Exception as e:
        print(json.dumps({"error": "FETCH_FAILED", "message": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
