#!/usr/bin/env python3
"""
kosha_auth.py — Multi-user Google token switcher for Kosha.

Kosha calls this script before any Google API request. It reads the current
session's platform + user_id, looks up the user's google_id in
user_mappings.json, copies their personal google_token.json into the shared
HERMES_HOME root, and prints their timezone so Kosha can display times correctly.

Usage:
    python kosha_auth.py --platform discord --user-id 1036621467502252164
    python kosha_auth.py --check

Exit codes:
    0 — token swapped successfully / already authenticated
    1 — user not linked (send connect link)
    2 — token file missing for linked user (send connect link)
    3 — bad arguments
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
import os

HERMES_HOME   = Path(os.environ.get("HERMES_HOME", "/data/.hermes"))
MAPPINGS_FILE = HERMES_HOME / "user_mappings.json"
SHARED_TOKEN  = HERMES_HOME / "google_token.json"
SCRIPTS_DIR   = HERMES_HOME / "skills" / "productivity" / "google-workspace" / "scripts"
CONNECT_URL   = os.environ.get(
    "KOSHA_BASE_URL",
    "https://hermes-agent-production-85bc.up.railway.app"
) + "/setup/api/auth/google"


def load_mappings() -> dict:
    if not MAPPINGS_FILE.exists():
        return {}
    with open(MAPPINGS_FILE) as f:
        return json.load(f)


def save_mappings(mappings: dict) -> None:
    with open(MAPPINGS_FILE, "w") as f:
        json.dump(mappings, f, indent=2)


def user_token_path(google_id: str) -> Path:
    return (
        HERMES_HOME / "users" / google_id
        / "skills" / "productivity" / "google-workspace" / "scripts"
        / "google_token.json"
    )


def fetch_calendar_timezone() -> str | None:
    """Fetch the user's timezone from Google Calendar settings.
    Returns timezone string like 'Asia/Jakarta' or None on failure."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from google_api import build_service
        service = build_service("calendar", "v3")
        setting = service.settings().get(setting="timezone").execute()
        return setting.get("value")
    except Exception as e:
        print(f"TIMEZONE_FETCH_FAILED: {e}", file=sys.stderr)
        return None


def swap_token(platform: str, user_id: str) -> int:
    """Copy this user's token to the shared root and print their timezone."""
    key = f"{platform}:{user_id}"
    mappings = load_mappings()

    if key not in mappings:
        print(f"NOT_LINKED: No Google account connected for {key}")
        print(f"CONNECT_URL: {CONNECT_URL}?platform={platform}&user_id={user_id}")
        return 1

    entry     = mappings[key]
    google_id = entry["google_id"]
    token_path = user_token_path(google_id)

    # Copy token to shared root
    if not token_path.exists():
        auth_path = HERMES_HOME / "users" / google_id / "auth.json"
        if auth_path.exists():
            with open(auth_path) as f:
                auth = json.load(f)
            token = auth.get("google")
            if token:
                SHARED_TOKEN.write_text(json.dumps(token, indent=2))
            else:
                print(f"TOKEN_MISSING: Token file not found for google_id={google_id}")
                print(f"CONNECT_URL: {CONNECT_URL}?platform={platform}&user_id={user_id}")
                return 2
        else:
            print(f"TOKEN_MISSING: Token file not found for google_id={google_id}")
            print(f"CONNECT_URL: {CONNECT_URL}?platform={platform}&user_id={user_id}")
            return 2
    else:
        shutil.copy2(token_path, SHARED_TOKEN)

    # Fetch timezone — use cached value if available, otherwise fetch from Google
    timezone = entry.get("timezone")
    if not timezone:
        timezone = fetch_calendar_timezone()
        if timezone:
            mappings[key]["timezone"] = timezone
            save_mappings(mappings)

    print(f"OK: Token swapped for {key} (google_id={google_id})")
    print(f"TIMEZONE: {timezone or 'UTC'}")
    return 0


def check_current() -> int:
    """Check if the current shared token is present."""
    if not SHARED_TOKEN.exists():
        print("NOT_AUTHENTICATED: No token at shared root")
        return 1
    with open(SHARED_TOKEN) as f:
        token = json.load(f)
    if not token.get("token"):
        print("TOKEN_CORRUPT: access_token field is empty")
        return 1
    print(f"AUTHENTICATED: Token present (email={token.get('email', 'unknown')})")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Kosha multi-user token switcher")
    parser.add_argument("--platform", help="Channel platform (discord, whatsapp)")
    parser.add_argument("--user-id",  help="Platform user ID")
    parser.add_argument("--check",    action="store_true", help="Check current token only")
    args = parser.parse_args()

    if args.check:
        sys.exit(check_current())

    if not args.platform or not args.user_id:
        print("ERROR: --platform and --user-id are required (or use --check)")
        sys.exit(3)

    sys.exit(swap_token(args.platform, args.user_id))


if __name__ == "__main__":
    main()
