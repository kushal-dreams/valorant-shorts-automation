
"""Clean up local MP4s for videos confirmed scheduled on YouTube."""

import json
import sqlite3
from pathlib import Path

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent
DB = ROOT / "shorts.db"
CONFIG = ROOT / "config.json"
CLIENT_SECRETS = ROOT / "client_secrets.json"
READONLY_TOKEN = ROOT / "readonly_token.json"

# Keep False for the first run. Review the preview before enabling deletion.
DELETE_FILES = True

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def get_youtube_client():
    credentials = None

    if READONLY_TOKEN.exists():
        credentials = Credentials.from_authorized_user_file(
            str(READONLY_TOKEN), SCOPES
        )

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            from google.auth.transport.requests import Request
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS), SCOPES
            )
            credentials = flow.run_local_server(port=0)

        READONLY_TOKEN.write_text(
            credentials.to_json(), encoding="utf-8"
        )

    return build("youtube", "v3", credentials=credentials)


def main():
    if not DB.is_file() or not CONFIG.is_file():
        print("ERROR: shorts.db or config.json is missing.")
        return

    if not CLIENT_SECRETS.is_file():
        print("ERROR: client_secrets.json is missing.")
        return

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    output_folder = Path(cfg.get("output_folder", "output"))

    if not output_folder.is_absolute():
        output_folder = ROOT / output_folder

    output_folder = output_folder.resolve()

    youtube = get_youtube_client()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    try:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(clips)")
        }

        required = {"status", "youtube_id", "output_path", "scheduled_at"}
        if not required.issubset(columns):
            print("ERROR: clips table is missing required columns.")
            return

        rows = con.execute("""
            SELECT output_path, youtube_id, scheduled_at, status
            FROM clips
            WHERE status IN ('scheduled', 'uploaded')
              AND youtube_id IS NOT NULL
              AND youtube_id != ''
              AND scheduled_at IS NOT NULL
              AND scheduled_at != ''
        """).fetchall()

        print("Mode:", "DELETE" if DELETE_FILES else "DRY RUN")
        print("Database records to check:", len(rows))

        deleted = 0
        kept = 0

        for row in rows:
            video_id = row["youtube_id"]
            raw_path = row["output_path"]

            if not raw_path:
                print("KEEP: no local path for", video_id)
                kept += 1
                continue

            path = Path(raw_path)
            if not path.is_absolute():
                path = ROOT / path
            path = path.resolve()

            # Only allow MP4s directly inside the configured output folder.
            if path.parent != output_folder or path.suffix.lower() != ".mp4":
                print("KEEP: path outside allowed output folder:", path)
                kept += 1
                continue

            if not path.is_file():
                print("KEEP: file not found:", path.name)
                kept += 1
                continue

            try:
                response = youtube.videos().list(
                    part="status",
                    id=video_id,
                ).execute()

                items = response.get("items", [])
                if not items:
                    print("KEEP: video not accessible:", video_id)
                    kept += 1
                    continue

                status = items[0].get("status", {})
                privacy = status.get("privacyStatus")
                publish_at = status.get("publishAt")

                if privacy != "private" or not publish_at:
                    print("KEEP: not confirmed as scheduled:", path.name)
                    kept += 1
                    continue

                action = "DELETE" if DELETE_FILES else "WOULD DELETE"
                print(f"{action}: {path.name} -> {video_id}")

                if DELETE_FILES:
                    path.unlink()
                    deleted += 1

            except Exception as exc:
                print(f"KEEP: verification failed for {video_id}: {exc}")
                kept += 1

        print("\nFinished.")
        print("Deleted:", deleted)
        print("Kept / not deleted:", kept)

    finally:
        con.close()


if __name__ == "__main__":
    main()
