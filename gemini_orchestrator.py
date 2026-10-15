import json
import math
import os
import subprocess

from pydantic import BaseModel, Field

from config import CROP_PROVIDER, GEMINI_FALLBACK_MODEL, GEMINI_MODEL


class CropCoordinates(BaseModel):
    start_time: float = Field(description="Start time of highlight in seconds")
    duration: float = Field(description="Duration of highlight in seconds")
    crop_x: int = Field(description="Top-left X coordinate of the 9:16 crop box")
    crop_y: int = Field(description="Top-left Y coordinate of the 9:16 crop box")
    crop_w: int = Field(description="Width of the 9:16 crop box")
    crop_h: int = Field(description="Height of the 9:16 crop box")


def deterministic_center_crop(width: int, height: int, total_duration: float = 15.0) -> CropCoordinates:
    aspect = 9.0 / 16.0
    crop_h = height
    crop_w = int(crop_h * aspect)
    if crop_w > width:
        crop_w = width
        crop_h = int(crop_w / aspect)
    crop_w = max(2, (crop_w // 2) * 2)
    crop_h = max(2, (crop_h // 2) * 2)
    return CropCoordinates(
        start_time=0.0,
        duration=min(max(float(total_duration), 0.1), 15.0),
        crop_x=max(0, ((width - crop_w) // 2) // 2 * 2),
        crop_y=max(0, ((height - crop_h) // 2) // 2 * 2),
        crop_w=crop_w,
        crop_h=crop_h,
    )


def _best_band_start(scores: list[int], band_size: int) -> int:
    band_size = max(1, min(len(scores), band_size))
    running = sum(scores[:band_size])
    best_score, best_start = running, 0
    for start in range(1, len(scores) - band_size + 1):
        running += scores[start + band_size - 1] - scores[start - 1]
        if running > best_score:
            best_score, best_start = running, start
    return best_start


def local_motion_crop(video_path: str, width: int, height: int, total_duration: float) -> tuple[CropCoordinates, str]:
    """Select a short crop from sampled motion using FFmpeg and Python stdlib only."""
    duration = min(max(float(total_duration), 0.0), 15.0)
    if width <= 0 or height <= 0 or not math.isfinite(duration) or duration <= 0:
        return deterministic_center_crop(width, height, duration), "center_fallback"

    sample_w, sample_h, sample_fps = 160, 90, 2
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", video_path, "-t", f"{duration:.3f}",
        "-an", "-vf",
        f"fps={sample_fps},scale={sample_w}:{sample_h},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    try:
        decoded = subprocess.run(command, capture_output=True, timeout=45, check=False)
        frame_size = sample_w * sample_h
        raw = decoded.stdout
        frame_count = len(raw) // frame_size
        if decoded.returncode != 0 or frame_count < 2:
            raise RuntimeError("Could not decode enough frames for local motion analysis.")

        frame_scores: list[int] = []
        column_scores = [0] * sample_w
        row_scores = [0] * sample_h
        for index in range(1, frame_count):
            previous = raw[(index - 1) * frame_size:index * frame_size]
            current = raw[index * frame_size:(index + 1) * frame_size]
            score = 0
            for y in range(sample_h):
                row_offset = y * sample_w
                row_motion = 0
                for x in range(sample_w):
                    delta = abs(current[row_offset + x] - previous[row_offset + x])
                    score += delta
                    row_motion += delta
                    column_scores[x] += delta
                row_scores[y] += row_motion
            frame_scores.append(score)

        highlight_duration = min(5.0, duration)
        window_pairs = min(len(frame_scores), max(1, round(highlight_duration * sample_fps)))
        best_index = max(
            range(len(frame_scores) - window_pairs + 1),
            key=lambda i: sum(frame_scores[i:i + window_pairs]),
        )
        start_time = min(max(0.0, best_index / sample_fps), max(0.0, duration - highlight_duration))

        crop_w = int(height * 9 / 16)
        crop_h = height
        if crop_w > width:
            crop_w = width
            crop_h = int(width * 16 / 9)
        crop_w = max(2, (crop_w // 2) * 2)
        crop_h = max(2, (crop_h // 2) * 2)
        band_w = max(1, round(crop_w * sample_w / width))
        band_h = max(1, round(crop_h * sample_h / height))
        crop_x = round(_best_band_start(column_scores, band_w) * width / sample_w)
        crop_y = round(_best_band_start(row_scores, band_h) * height / sample_h)
        crop_x = max(0, min((crop_x // 2) * 2, width - crop_w))
        crop_y = max(0, min((crop_y // 2) * 2, height - crop_h))

        return CropCoordinates(
            start_time=start_time,
            duration=highlight_duration,
            crop_x=crop_x,
            crop_y=crop_y,
            crop_w=crop_w,
            crop_h=crop_h,
        ), "local_motion_heuristic"
    except Exception as error:
        print(f"[Local crop] Motion analysis unavailable ({error}); using a centered crop.")
        return deterministic_center_crop(width, height, duration), "center_fallback"


def _gemini_crop(video_path: str, width: int, height: int, total_duration: float) -> CropCoordinates:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    client = genai.Client(api_key=api_key)
    uploaded_file = client.files.upload(file=video_path)
    try:
        prompt = (
            f"Analyze this video of resolution {width}x{height} with duration {total_duration:.2f}s. "
            "Identify the primary kinetic action and specify a 9:16 crop box and highlight time range. "
            f"Keep coordinates inside {width}x{height}."
        )
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CropCoordinates,
            temperature=0.2,
        )
        response = None
        errors = []
        models = [GEMINI_MODEL]
        if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
            models.append(GEMINI_FALLBACK_MODEL)
        for model_name in models:
            try:
                response = client.models.generate_content(
                    model=model_name, contents=[uploaded_file, prompt], config=config
                )
                break
            except Exception as error:
                errors.append(f"{model_name}: {error}")
        if response is None:
            raise RuntimeError("; ".join(errors))
        coords = CropCoordinates(**json.loads(response.text))
        if coords.crop_w <= 0 or coords.crop_h <= 0 or coords.crop_w > width or coords.crop_h > height:
            raise ValueError("Gemini returned crop dimensions outside the video bounds.")
        if coords.crop_x < 0 or coords.crop_y < 0 or coords.crop_x + coords.crop_w > width or coords.crop_y + coords.crop_h > height:
            raise ValueError("Gemini returned crop coordinates outside the video bounds.")
        if coords.start_time < 0 or coords.duration <= 0 or coords.start_time >= total_duration:
            raise ValueError("Gemini returned an invalid highlight time range.")

        crop_w = min(coords.crop_w, width)
        crop_h = min(coords.crop_h, height)
        target_w = int(crop_h * 9 / 16)
        if target_w <= width:
            crop_w = target_w
        else:
            crop_h = int(crop_w * 16 / 9)
        crop_w = max(2, (crop_w // 2) * 2)
        crop_h = max(2, (crop_h // 2) * 2)
        start_time = min(coords.start_time, total_duration)
        duration = min(coords.duration, total_duration - start_time)
        return CropCoordinates(
            start_time=start_time,
            duration=duration,
            crop_x=max(0, min(coords.crop_x, width - crop_w)) // 2 * 2,
            crop_y=max(0, min(coords.crop_y, height - crop_h)) // 2 * 2,
            crop_w=crop_w,
            crop_h=crop_h,
        )
    finally:
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass


def analyze_and_crop(video_path: str, width: int, height: int, total_duration: float = 15.0) -> tuple[CropCoordinates, str]:
    # Offline motion framing is default: no API credentials or network required.
    # Gemini can be explicitly enabled with CROP_PROVIDER=gemini.
    if CROP_PROVIDER == "gemini":
        try:
            return _gemini_crop(video_path, width, height, total_duration), "gemini"
        except Exception as error:
            print(f"[Gemini] Unavailable ({error}); switching to local motion framing.")
    return local_motion_crop(video_path, width, height, total_duration)
