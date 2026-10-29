# Valorant Shorts Automation

An AI-assisted Python workflow for rendering, reviewing, tracking, and scheduling Valorant YouTube Shorts.

---

## Motivation

I play Valorant and record gameplay clips with [Medal.tv](https://medal.tv). Turning raw clips into polished YouTube Shorts — cropping to 9:16, adding music, uploading, scheduling — was tedious and repetitive. I built this project to automate the entire pipeline: watch a clips folder, render Shorts-ready videos with FFmpeg, track every clip in a local database to prevent duplicates, and optionally upload and schedule them to YouTube via the Data API.

The goal was a system I could leave running on my PC that would turn new Medal clips into upload-ready Shorts with minimal manual intervention, while keeping me in control of what actually gets published.

## How It Works

```
Medal clips folder
       │
       ▼
   main.py  ─── polls for new .mp4/.mov/.mkv/.webm files
       │         waits for each file to finish writing
       │         fingerprints with SHA-256 to prevent duplicates
       ▼
  renderer.py ── FFmpeg pipeline:
       │           • splits video into blurred 9:16 background + sharp gameplay panel
       │           • overlays gameplay centered on the blurred backdrop
       │           • mixes in a randomly selected music track with game audio
       │           • applies fade-in/fade-out transitions
       │           • encodes H.264 + AAC with configurable quality
       ▼
   shorts.db ─── SQLite tracking: fingerprint, status, paths, timestamps, errors
       │
       ├── review.py ──── interactive CLI to approve/reject rendered clips
       │                   rejected clips are permanently excluded from re-rendering
       │
       ├── publish.py ─── continuous scheduler: uploads rendered clips as private
       │                   YouTube videos with scheduled publish times (1 PM / 9 PM IST)
       │                   deletes local MP4 only after confirmed upload + DB commit
       │
       └── cleanup_scheduled.py ── verifies scheduled videos exist on YouTube via
                                    read-only API, then deletes confirmed local copies
```

## Features

- **Automatic clip detection** — continuously polls a configurable Medal clips folder for new video files.
- **Stability check** — waits for files to finish writing before processing (important for Medal's save behavior).
- **SHA-256 fingerprinting** — every source clip is hashed; duplicates are never re-rendered or re-uploaded.
- **FFmpeg rendering pipeline** — produces 1080×1920 (9:16) Shorts with:
  - Blurred background fill from the original gameplay footage.
  - Centered sharp gameplay panel (configurable ratio).
  - Optional motion blur approximation via frame blending.
  - Random music track selection with anti-repeat logic.
  - Game audio + music mixing with per-channel volume control.
  - Configurable fade-in/fade-out transitions.
  - High-quality H.264 encoding with tunable CRF and preset.
- **SQLite clip tracking** — persistent database records status (`rendered`, `uploaded`, `scheduled`, `rejected`, `error`), timestamps, YouTube video IDs, and error messages. Schema auto-migrates on startup.
- **Daily caps** — configurable limits for both renders and uploads per calendar day.
- **Interactive review CLI** (`review.py`) — list rendered clips, reject unwanted ones (move to `rejected/` or delete), permanently exclude source clips from future processing.
- **Passive rejection** — manually deleting a file from `output/` is detected and treated as a rejection.
- **YouTube upload** (`youtube_upload.py`) — OAuth 2.0 Desktop flow with resumable chunked uploads via the YouTube Data API v3. Supports `private`, `unlisted`, and `public` privacy modes.
- **Scheduled publishing** (`publish.py`) — uploads rendered clips as private videos with a future `publishAt` timestamp. Targets 1 PM and 9 PM IST slots, automatically avoiding collisions with already-scheduled videos. Local MP4 is deleted only after the upload is confirmed and the database transaction is committed.
- **Post-upload cleanup** (`cleanup_scheduled.py`) — uses a read-only YouTube API scope to verify that scheduled videos exist and are confirmed private with a publish time, then deletes the local copy.
- **Safety defaults** — uploads are disabled by default; publishing requires an explicit config flag; privacy defaults to `private`.
- **Structured logging** — timestamped log file in `logs/automation.log` plus console output.

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Video processing | FFmpeg (subprocess), ffprobe |
| YouTube API | `google-api-python-client`, OAuth 2.0 Desktop flow |
| Database | SQLite 3 (via `sqlite3` stdlib) |
| Scheduling | Python `datetime` + `zoneinfo` (IST time slots) |
| Hashing | SHA-256 (`hashlib`) |

## Installation (Windows)

### Prerequisites

- **Python 3.11 or newer** — [python.org](https://www.python.org/downloads/)
- **FFmpeg** — download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to your system PATH. Verify with:
  ```bat
  ffmpeg -version
  ffprobe -version
  ```
- **Medal.tv** (or any screen recorder that saves clips as `.mp4`/`.mov`/`.mkv`/`.webm` to a local folder)

### Setup

```bat
git clone https://github.com/kushal-dreams/valorant-shorts-automation.git
cd valorant-shorts-automation

python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Configuration

1. Copy the example config:
   ```bat
   copy config.example.json config.json
   ```
2. Edit `config.json` and set `medal_folder` to your actual Medal clips path:
   ```json
   "medal_folder": "C:/Users/YourName/Videos/Medal"
   ```
   Use forward slashes in the path.

3. Add music tracks (`.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`) to the `music/` folder. Use only tracks you have permission to use (e.g., NCS releases).

### YouTube API Setup (optional — only needed for uploads)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project.
2. Enable **YouTube Data API v3**.
3. Configure the **OAuth consent screen** (External, add your Google account as a test user if the app is unverified).
4. Create an **OAuth 2.0 Client ID** → Desktop application.
5. Download the JSON credentials file and save it as `client_secrets.json` in the project root.
6. On first upload, a browser window will open for OAuth authorization. The resulting token is saved locally as `token.json`.

> **Note:** Unverified Google API projects have upload quotas and may restrict public publishing. Test with `privacy_status: "private"` first.

## Usage

### Render clips (upload disabled by default)

```bat
.venv\Scripts\python main.py
```

The watcher polls your Medal folder, renders new clips into `output/`, and logs progress. Stop with `Ctrl+C`.

### Review rendered clips

```bat
.venv\Scripts\python review.py
```

Lists all rendered clips. Enter a number to reject a specific clip, `A` to reject all, or `Q` to quit. Rejected source clips are permanently excluded from re-processing.

### Enable YouTube uploads

In `config.json`, set:
```json
"upload_enabled": true,
"privacy_status": "private"
```

Then run `main.py`. Uploads happen automatically after rendering. Change `privacy_status` to `"public"` only after verifying your API project supports it.

### Schedule uploads for specific times

```bat
.venv\Scripts\python publish.py
```

Requires `"publishing_enabled": true` in `config.json`. Uploads rendered clips as private videos scheduled to go public at 1 PM and 9 PM IST. Local MP4s are deleted only after confirmed upload.

### Cleanup confirmed uploads

```bat
.venv\Scripts\python cleanup_scheduled.py
```

Verifies scheduled videos exist on YouTube (read-only API), then deletes confirmed local copies. Review mode is available by setting `DELETE_FILES = False` in the script.

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `medal_folder` | *(required)* | Path to your Medal clips folder |
| `output_folder` | `"output"` | Where rendered Shorts are saved |
| `music_folder` | `"music"` | Folder containing background music tracks |
| `poll_seconds` | `30` | How often to check for new clips (seconds) |
| `max_clips_per_day` | `5` | Maximum clips to render per calendar day (0 = unlimited) |
| `max_uploads_per_day` | `5` | Maximum YouTube uploads per calendar day |
| `upload_enabled` | `false` | Enable automatic YouTube uploads |
| `publishing_enabled` | `false` | Enable the `publish.py` scheduler |
| `privacy_status` | `"private"` | YouTube privacy: `private`, `unlisted`, or `public` |
| `title_prefix` | `"Valorant"` | Prefix for auto-generated video titles |
| `description` | *(see config)* | YouTube video description |
| `music_volume` | `0.35` | Music track volume (0.0–1.0) |
| `game_volume` | `1.0` | Game audio volume (0.0–1.0) |
| `width` | `1080` | Output video width |
| `height` | `1920` | Output video height |
| `panel_ratio` | `0.5` | Gameplay panel height as a fraction of total height |
| `preset` | `"veryfast"` | FFmpeg x264 encoding preset |
| `crf` | `21` | FFmpeg CRF quality (lower = higher quality, larger file) |
| `fade_in` | `0.2` | Fade-in duration (seconds) |
| `fade_out` | `0.3` | Fade-out duration (seconds) |
| `motion_blur_enabled` | `false` | Enable frame-blending motion blur effect |
| `hook_enabled` | `false` | Enable opening text hook overlay |
| `hook_text` | `"WAIT FOR IT..."` | Text shown during the hook |

## Limitations & Known Issues

- **Titles are filename-based** — titles are derived from the source clip filename, not from gameplay analysis. Rename clips in Medal for better titles.
- **No AI gameplay analysis** — the system does not identify kills, aces, or highlight moments. It renders the full clip duration.
- **No speech captions or beat-sync** — captions, subtitles, and beat-synchronized editing are not implemented.
- **Audio stream required** — clips without an audio stream may fail during rendering.
- **Windows-only tested** — developed and tested on Windows. The code uses standard Python and FFmpeg, so it may work on other platforms with path adjustments, but this is untested.
- **YouTube API quotas** — unverified Google API projects have strict upload quotas. Public publishing may be restricted.
- **Single-machine** — the watcher must run on a machine that is on, awake, and connected to the internet.
- **Generic hook text** — the optional hook overlay shows the same text for every clip. Content-aware hooks are not implemented.

## AI Disclosure

This project was developed with assistance from AI coding tools (GitHub Copilot, Claude). I iterated on the implementation, debugged issues, and made architectural decisions throughout the process. The AI tools helped accelerate development but the project design, feature choices, and testing were driven by me.

## License

This project is provided as-is for educational and portfolio purposes.
