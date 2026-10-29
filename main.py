import hashlib
import json
import logging
import random
import shutil
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from renderer import render_clip
from youtube_upload import upload_video

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DB_PATH = ROOT / "shorts.db"


def load_config():
    if not CONFIG_PATH.exists():
        example = ROOT / "config.example.json"
        shutil.copy(example, CONFIG_PATH)
        raise SystemExit(
            "Created config.json from config.example.json. Edit it with your Medal folder path, "
            "then run this script again."
        )
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def setup_logging():
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(ROOT / "logs" / "automation.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            fingerprint TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            output_path TEXT,
            status TEXT NOT NULL,
            youtube_id TEXT,
            processed_at TEXT,
            uploaded_at TEXT,
            error TEXT
        )
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(clips)").fetchall()}
    if "music_path" not in columns:
        con.execute("ALTER TABLE clips ADD COLUMN music_path TEXT")
    if "scheduled_at" not in columns:
        con.execute("ALTER TABLE clips ADD COLUMN scheduled_at TEXT")
    if "title" not in columns:
        con.execute("ALTER TABLE clips ADD COLUMN title TEXT")
    con.commit()
    return con


def fingerprint(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_until_stable(path: Path, checks=3, delay=3):
    previous = None
    stable = 0
    for _ in range(60):
        try:
            current = path.stat().st_size
        except OSError:
            time.sleep(delay)
            continue
        if current > 0 and current == previous:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
        previous = current
        time.sleep(delay)
    return False


def daily_upload_count(con):
    today = date.today().isoformat()
    row = con.execute(
        "SELECT COUNT(*) FROM clips WHERE status='uploaded' AND uploaded_at LIKE ?",
        (today + "%",)
    ).fetchone()
    return row[0]


def daily_processed_count(con):
    """Count clips rendered/reviewed today, independently of the upload cap."""
    today = date.today().isoformat()
    row = con.execute(
        "SELECT COUNT(*) FROM clips WHERE processed_at LIKE ? "
        "AND status IN ('rendered','uploaded','scheduled','rejected','error')", (today + "%",)
    ).fetchone()
    return row[0]


def reject_missing_outputs(con):
    """A manually deleted rendered file is a rejection, not permission to rerender it."""
    rows = con.execute(
        "SELECT fingerprint, output_path FROM clips WHERE status='rendered'"
    ).fetchall()
    for fp, output_path in rows:
        if not output_path or not Path(output_path).exists():
            con.execute(
                "UPDATE clips SET status='rejected', error=? WHERE fingerprint=?",
                ("Output removed from output folder; treated as rejected and will not be reused.", fp)
            )
    con.commit()


def choose_music(con, music_files):
    """Pick a random track while avoiding immediate repeats where possible."""
    music_files = sorted(music_files, key=lambda p: p.name.lower())
    if len(music_files) < 2:
        return music_files[0]
    try:
        row = con.execute("SELECT music_path FROM clips WHERE music_path IS NOT NULL ORDER BY processed_at DESC LIMIT 1").fetchone()
        last = row[0] if row else None
    except sqlite3.OperationalError:
        last = None
    choices = [p for p in music_files if str(p.resolve()) != last]
    return random.choice(choices or music_files)


def make_title(path: Path, config):
    # Safe baseline: uses the clip filename, rather than inventing what happened in the gameplay.
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    stem = " ".join(stem.split())
    prefix = config.get("title_prefix", "Valorant")
    title = f"{prefix} | {stem}" if stem else f"{prefix} gameplay"
    return title[:100]


def main():
    setup_logging()
    cfg = load_config()
    con = connect_db()

    source_dir = Path(cfg["medal_folder"]).expanduser()
    output_dir = ROOT / cfg.get("output_folder", "output")
    music_dir = ROOT / cfg.get("music_folder", "music")
    output_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    if not source_dir.exists():
        raise SystemExit(f"Medal folder does not exist: {source_dir}")

    extensions = {".mp4", ".mov", ".mkv", ".webm"}
    max_daily = int(cfg.get("max_uploads_per_day", 5))
    max_clips_per_day = int(cfg.get("max_clips_per_day", 5))
    poll_seconds = int(cfg.get("poll_seconds", 30))
    upload_enabled = bool(cfg.get("upload_enabled", False))
    publish_mode = cfg.get("privacy_status", "private")
    if publish_mode not in {"private", "unlisted", "public"}:
        raise SystemExit("privacy_status must be private, unlisted, or public.")

    logging.info("Watching Medal folder: %s", source_dir)
    logging.info("Upload enabled: %s | privacy: %s | upload cap/day: %s | render cap/day: %s",
                 upload_enabled, publish_mode, max_daily, max_clips_per_day)

    while True:
        try:
            reject_missing_outputs(con)
            # Upload already-rendered clips first, so clips waiting behind the daily cap
            # are picked up on the next day/run.
            if upload_enabled and daily_upload_count(con) < max_daily:
                pending = con.execute(
                    "SELECT fingerprint, output_path, source_path FROM clips "
                    "WHERE status='rendered' ORDER BY processed_at ASC"
                ).fetchall()
                for fp_pending, output_pending, source_pending in pending:
                    if daily_upload_count(con) >= max_daily:
                        break
                    out_path = Path(output_pending) if output_pending else None
                    if not out_path or not out_path.exists():
                        con.execute(
                            "UPDATE clips SET status='error', error=? WHERE fingerprint=?",
                            ("Rendered output file is missing.", fp_pending)
                        )
                        con.commit()
                        continue
                    try:
                        title = make_title(Path(source_pending), cfg)
                        description = cfg.get(
                            "description",
                            "Valorant gameplay clip. Music and gameplay are edited for this Short."
                        )
                        video_id = upload_video(
                            out_path, title, description, publish_mode,
                            ROOT / "client_secrets.json", ROOT / "token.json"
                        )
                        con.execute(
                            "UPDATE clips SET status='uploaded', youtube_id=?, uploaded_at=?, error=NULL "
                            "WHERE fingerprint=?",
                            (video_id, datetime.now().isoformat(timespec="seconds"), fp_pending)
                        )
                        con.commit()
                        logging.info("Queued upload complete: https://www.youtube.com/shorts/%s", video_id)
                    except Exception as exc:
                        logging.exception("Queued upload failed for %s", out_path)
                        con.execute(
                            "UPDATE clips SET error=? WHERE fingerprint=?",
                            (str(exc), fp_pending)
                        )
                        con.commit()

            candidates = sorted(
                (p for p in source_dir.rglob("*")
                 if p.is_file() and p.suffix.lower() in extensions),
                key=lambda p: p.stat().st_mtime
            )
            for source in candidates:
                # Avoid repeatedly waiting on files that have already been processed.
                prior = con.execute(
                    "SELECT status FROM clips WHERE source_path=? ORDER BY processed_at DESC LIMIT 1",
                    (str(source),)
                ).fetchone()
                if prior and prior[0] in {"rendered", "uploaded", "scheduled", "skipped", "rejected"}:
                    continue

                
                if max_clips_per_day > 0 and daily_processed_count(con) >= max_clips_per_day:
                    logging.info(
                        "Daily render limit reached (%s/%s). Waiting for the next day.",
                        daily_processed_count(con),
                        max_clips_per_day
                    )
                    break


                if not wait_until_stable(source):
                    continue

                try:
                    fp = fingerprint(source)
                except OSError:
                    continue

                row = con.execute(
                    "SELECT status FROM clips WHERE fingerprint=?", (fp,)
                ).fetchone()
                if row and row[0] in {"rendered", "uploaded", "scheduled", "skipped", "rejected"}:
                    continue

                music_files = [
                    p for p in music_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
                ]
                if not music_files:
                    logging.warning("No music tracks found in %s; waiting for music files.", music_dir)
                    break

                output_path = output_dir / f"{source.stem[:70]}_short.mp4"
                try:
                    chosen_music = choose_music(con, music_files)
                    logging.info("Rendering: %s | music selected: %s", source.name, chosen_music.name)
                    render_clip(source, chosen_music, output_path, cfg)
                    con.execute("""
                        INSERT OR REPLACE INTO clips
                        (fingerprint, source_path, output_path, status, processed_at, error, music_path)
                        VALUES (?, ?, ?, 'rendered', ?, NULL, ?)
                    """, (fp, str(source), str(output_path), datetime.now().isoformat(timespec="seconds"), str(chosen_music.resolve())))
                    con.commit()
                    logging.info("Rendered: %s | music: %s", output_path, chosen_music.name)
                except Exception as exc:
                    logging.exception("Render failed for %s", source)
                    con.execute("""
                        INSERT OR REPLACE INTO clips
                        (fingerprint, source_path, status, processed_at, error)
                        VALUES (?, ?, 'error', ?, ?)
                    """, (fp, str(source), datetime.now().isoformat(timespec="seconds"), str(exc)))
                    con.commit()
                    continue

                if upload_enabled and daily_upload_count(con) < max_daily:
                    try:
                        title = make_title(source, cfg)
                        description = cfg.get(
                            "description",
                            "Valorant gameplay clip. Music and gameplay are edited for this Short."
                        )
                        video_id = upload_video(
                            output_path, title, description, publish_mode,
                            ROOT / "client_secrets.json", ROOT / "token.json"
                        )
                        con.execute("""
                            UPDATE clips SET status='uploaded', youtube_id=?, uploaded_at=?, error=NULL
                            WHERE fingerprint=?
                        """, (video_id, datetime.now().isoformat(timespec="seconds"), fp))
                        con.commit()
                        logging.info("Upload complete: https://www.youtube.com/shorts/%s", video_id)
                    except Exception as exc:
                        logging.exception("Upload failed for %s", output_path)
                        con.execute(
                            "UPDATE clips SET error=? WHERE fingerprint=?",
                            (str(exc), fp)
                        )
                        con.commit()
                elif upload_enabled:
                    logging.info("Daily upload limit reached; rendered clip queued locally.")
                else:
                    logging.info("Upload disabled. Rendered clip is ready for review.")
        except Exception:
            logging.exception("Watcher loop error")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
