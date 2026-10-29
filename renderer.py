
import json
import subprocess
from pathlib import Path


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg failed.\nCommand: " + " ".join(map(str, cmd)) +
            "\n\n" + result.stderr[-5000:]
        )


def probe_duration(source: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError(f"Invalid video duration: {source}")
    return duration


def has_audio(source: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def render_clip(source: Path, music: Path, output: Path, config: dict):
    source = Path(source)
    music = Path(music)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    music_volume = float(config.get("music_volume", 0.35))
    game_volume = float(config.get("game_volume", 1.0))
    width = int(config.get("width", 1080))
    height = int(config.get("height", 1920))

    panel_ratio = float(config.get("panel_ratio", 0.50))
    panel_height = int(height * panel_ratio)
    panel_height -= panel_height % 2

    if not (0 < panel_ratio <= 1):
        raise ValueError("panel_ratio must be greater than 0 and at most 1")
    if width % 2 or height % 2:
        raise ValueError("Output width and height must be even numbers")

    duration = probe_duration(source)
    fade_in = min(float(config.get("fade_in", 0.20)), duration / 3)
    fade_out = min(float(config.get("fade_out", 0.30)), duration / 3)
    fade_out_start = max(0.0, duration - fade_out)

    source_has_audio = has_audio(source)
    motion_blur_enabled = bool(config.get("motion_blur_enabled", True))

    # Blend two neighboring frames for a subtle motion-blur approximation.
    # This can produce mild ghosting; disable it for maximum gameplay sharpness.
    if motion_blur_enabled:
        gameplay_filter = (
            "tmix=frames=2:weights='1 1':scale=0.5,"
        )
    else:
        gameplay_filter = ""

    video_filter = (
        f"[0:v]split=2[bgraw][fgraw];"
        f"[bgraw]scale={width}:{height}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=22:8[bgout];"
        f"[fgraw]{gameplay_filter}"
        f"scale={width}:{panel_height}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={width}:{panel_height},"
        f"setsar=1[fgout];"
        f"[bgout][fgout]overlay="
        f"(W-w)/2:(H-h)/2,"
        f"fade=t=in:st=0:d={fade_in:.3f},"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f},"
        f"format=yuv420p[v]"
    )

    music_filter = (
        f"[1:a]volume={music_volume},"
        f"afade=t=in:st=0:d={fade_in:.3f}[music]"
    )

    if source_has_audio:
        audio_filter = (
            f"{music_filter};"
            f"[0:a]volume={game_volume}[game];"
            f"[game][music]amix=inputs=2:duration=first:"
            f"dropout_transition=2,"
            f"alimiter=limit=0.95,"
            f"afade=t=out:st={fade_out_start:.3f}:"
            f"d={fade_out:.3f}[a]"
        )
    else:
        audio_filter = (
            f"{music_filter};"
            f"[music]alimiter=limit=0.95,"
            f"afade=t=out:st={fade_out_start:.3f}:"
            f"d={fade_out:.3f}[a]"
        )

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(source),
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", video_filter + ";" + audio_filter,
        "-map", "[v]",
        "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", config.get("preset", "slow"),
        "-crf", str(config.get("crf", 16)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "320k",
        "-movflags", "+faststart",
        str(output),
    ]

    run(cmd)
