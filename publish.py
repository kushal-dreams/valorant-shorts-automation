"""Continuous scheduler for approved Valorant Shorts.
Deletes local MP4s only after upload and scheduling are confirmed
and the scheduled status is committed to the database.
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = ROOT / "shorts.db"
CONFIG = ROOT / "config.json"
TZ = ZoneInfo("Asia/Kolkata")
SLOTS = [(13, 0), (21, 0)]

EDITORIAL_TITLES = [
    "Ethereal", "Sovereign", "Lucidity", "Apex",
    "Eclipse", "Axiom", "Phantom", "Relentless",
    "Zenith", "Ascendant", "Oblivion", "Unrivaled",
    "Ephemeral", "Tenacity", "Dominion", "Ineffable",
]


def metadata(path, index=0):
    import hashlib

    digest = int(
        hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12], 16
    )
    title = EDITORIAL_TITLES[digest % len(EDITORIAL_TITLES)]
    description = (
        "If you enjoy fast aim and flicking make sure to like and subscribe :)\n\n"
        "@mrbanana062\n\n"
        "#shorts #valorant #valorantmontage #mrbanana062"
    )
    tags = ["VALORANT", "Valorant montage", "FPS gaming", "mrbanana062"]
    return title, description, tags


def next_slots(count, con):
    now = datetime.now(TZ)
    result = []

    existing = {
        row[0]
        for row in con.execute("""
            SELECT scheduled_at
            FROM clips
            WHERE scheduled_at IS NOT NULL
              AND scheduled_at != ''
              AND status IN ('scheduled', 'uploaded')
        """).fetchall()
    }

    day = now.date()

    while len(result) < count:
        for hour, minute in SLOTS:
            candidate = datetime(
                day.year, day.month, day.day,
                hour, minute, tzinfo=TZ
            )

            if candidate <= now + timedelta(minutes=30):
                continue

            if candidate.isoformat() in existing:
                continue

            if candidate not in result:
                result.append(candidate)

            if len(result) >= count:
                break

        day += timedelta(days=1)

    return result


def main():
    if not DB.exists():
        print("No shorts.db found. Run main.py first.")
        return

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

    if not cfg.get("publishing_enabled", False):
        print("SAFE MODE: Set publishing_enabled=true in config.json.")
        return

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    cols = {r[1] for r in con.execute("PRAGMA table_info(clips)")}

    for name, definition in [
        ("scheduled_at", "TEXT"),
        ("title", "TEXT"),
    ]:
        if name not in cols:
            con.execute(f"ALTER TABLE clips ADD COLUMN {name} {definition}")

    con.commit()

    print("Continuous Shorts scheduler started.")
    print("Target: 1 PM and 9 PM IST.")
    print("Local MP4s are deleted only after confirmed scheduling.")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            rows = con.execute("""
                SELECT fingerprint, output_path, scheduled_at
                FROM clips
                WHERE status='rendered'
                  AND (scheduled_at IS NULL OR scheduled_at='')
                ORDER BY processed_at ASC
            """).fetchall()

            eligible = []
            for row in rows:
                path = Path(row["output_path"]) if row["output_path"] else None
                if path and path.is_file():
                    eligible.append((row, path))

            if not eligible:
                print("No eligible rendered clips. Checking again in 60 seconds.")
                time.sleep(60)
                continue

            slots = next_slots(len(eligible), con)

            for (row, path), slot in zip(eligible, slots):
                current = con.execute(
                    "SELECT status, scheduled_at FROM clips WHERE fingerprint=?",
                    (row["fingerprint"],),
                ).fetchone()

                if not current or current["status"] != "rendered":
                    continue
                if current["scheduled_at"]:
                    continue

                title, description, tags = metadata(path)
                publish_at_utc = (
                    slot.astimezone(ZoneInfo("UTC"))
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )

                print(f"\nPreparing: {path.name}")
                print(f"Title: {title}")
                print(f"Scheduled: {slot.strftime('%a %d %b %Y, %I:%M %p IST')}")

                try:
                    from youtube_upload import upload_video

                    video_id = upload_video(
                        path,
                        title,
                        description,
                        "private",
                        ROOT / "client_secrets.json",
                        ROOT / "token.json",
                        tags=tags,
                        publish_at=publish_at_utc,
                    )

                    if not video_id:
                        raise RuntimeError(
                            "YouTube did not return a video ID; keeping local file."
                        )

                    # Commit scheduling metadata before deleting the local file.
                    con.execute("""
                        UPDATE clips
                        SET status='scheduled',
                            youtube_id=?,
                            scheduled_at=?,
                            title=?,
                            error=NULL
                        WHERE fingerprint=? AND status='rendered'
                    """, (
                        video_id,
                        slot.isoformat(),
                        title,
                        row["fingerprint"],
                    ))

                    if con.execute("SELECT changes()").fetchone()[0] != 1:
                        con.rollback()
                        raise RuntimeError(
                            "Database status was not updated; local file retained."
                        )

                    con.commit()

                    print(f"Scheduled: https://www.youtube.com/shorts/{video_id}")

                    # Cleanup only after the DB commit succeeds.
                    try:
                        path.unlink()
                        print(f"Deleted local MP4: {path.name}")
                    except OSError as cleanup_error:
                        print(
                            f"Scheduled successfully, but could not delete "
                            f"{path.name}: {cleanup_error}"
                        )
                        print("You can delete this file manually later.")

                except Exception as exc:
                    import traceback
                    traceback.print_exc()

                    try:
                        con.rollback()
                        con.execute(
                            "UPDATE clips SET error=? WHERE fingerprint=?",
                            (str(exc), row["fingerprint"]),
                        )
                        con.commit()
                    except sqlite3.Error as db_error:
                        print(f"Could not save error to database: {db_error}")

                    print(f"FAILED: {path.name}: {exc}")
                    print("Local file retained. Review before retrying.")

            print("\nQueue checked. Checking again in 60 seconds.")
            time.sleep(60)

    except KeyboardInterrupt:
        print("\nScheduler stopped. Saved scheduling history is preserved.")
    finally:
        con.close()


if __name__ == "__main__":
    main()