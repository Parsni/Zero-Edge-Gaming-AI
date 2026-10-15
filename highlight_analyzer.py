"""Local, deterministic activity-window ranking for gameplay video previews.

This ranks visual change and relative audio peaks. It is deliberately not an
event classifier: it cannot identify kills, goals, wins, or human intent.
"""

import math
import statistics
import subprocess


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))]


def _motion_samples(video_path: str, duration: float) -> list[float]:
    width, height = 80, 45
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", video_path, "-t", f"{duration:.3f}",
        "-an", "-vf", f"fps=2,scale={width}:{height}:flags=area,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    frame_size = width * height
    frame_count = len(result.stdout) // frame_size
    if result.returncode != 0 or frame_count < 2:
        raise RuntimeError("Could not decode enough video frames to rank activity.")
    frames = [result.stdout[i * frame_size:(i + 1) * frame_size] for i in range(frame_count)]
    return [
        sum(abs(a - b) for a, b in zip(frames[i - 1], frames[i])) / (frame_size * 255.0)
        for i in range(1, frame_count)
    ]


def _audio_loudness_samples(video_path: str, duration: float) -> list[float]:
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", video_path, "-t", f"{duration:.3f}",
        "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    if result.returncode != 0 or len(result.stdout) < 8000:
        return []
    samples = memoryview(result.stdout).cast("h")
    block_size = 4000  # half a second at 8 kHz
    loudness = []
    for offset in range(0, len(samples) - block_size + 1, block_size):
        block = samples[offset:offset + block_size]
        rms = math.sqrt(sum(sample * sample for sample in block) / block_size)
        loudness.append(20 * math.log10(max(rms, 1) / 32768))
    return loudness


def suggest_highlight(video_path: str, total_duration: float, target_duration: float = 10.0) -> dict:
    """Return the highest-ranked window and non-overlapping alternatives."""
    duration = float(total_duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video duration must be positive and finite.")
    window = min(float(target_duration), duration)
    try:
        motion = _motion_samples(video_path, duration)
    except Exception as error:
        start = max(0.0, (duration - window) / 2)
        return {
            "start_time_seconds": round(start, 2),
            "end_time_seconds": round(start + window, 2),
            "duration_seconds": round(window, 2),
            "activity_score": 0.0,
            "motion_score": 0.0,
            "audio_peak_score": 0.0,
            "selection_method": "center_fallback",
            "reason": f"Motion analysis could not decode this file; selected a centered {window:.0f}s segment. {error}",
            "alternatives": [],
        }

    audio = _audio_loudness_samples(video_path, duration)
    audio_median = statistics.median(audio) if audio else 0.0
    audio_prominence = [max(0.0, value - audio_median) for value in audio]
    motion_scale = max(_percentile(motion, 0.95), 1e-6)
    audio_scale = max(_percentile(audio_prominence, 0.95), 1e-6)
    ticks = min(len(motion), len(audio_prominence)) if audio_prominence else len(motion)
    tick_seconds = 0.5
    window_ticks = max(1, min(ticks, round(window / tick_seconds)))
    last_start = max(0, math.floor((duration - window) / tick_seconds))
    step_ticks = 2  # evaluate start times at 1-second intervals
    starts = list(range(0, last_start + 1, step_ticks))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)

    scored = []
    for start_tick in starts:
        end_tick = min(ticks, start_tick + window_ticks)
        if end_tick <= start_tick:
            continue
        motion_value = statistics.fmean(motion[start_tick:min(len(motion), end_tick)])
        audio_value = statistics.fmean(audio_prominence[start_tick:min(len(audio_prominence), end_tick)]) if audio_prominence else 0.0
        motion_score = min(1.0, motion_value / motion_scale)
        audio_score = min(1.0, audio_value / audio_scale) if audio_prominence else 0.0
        total_score = 0.75 * motion_score + 0.25 * audio_score
        start_seconds = min(start_tick * tick_seconds, max(0.0, duration - window))
        scored.append({
            "start_time_seconds": round(start_seconds, 2),
            "end_time_seconds": round(start_seconds + window, 2),
            "duration_seconds": round(window, 2),
            "activity_score": round(total_score, 4),
            "motion_score": round(motion_score, 4),
            "audio_peak_score": round(audio_score, 4),
            "selection_method": "local_motion_audio_activity_heuristic",
        })

    if not scored:
        raise RuntimeError("No candidate windows could be measured.")
    ranked = sorted(scored, key=lambda item: (-item["activity_score"], item["start_time_seconds"]))
    selected = ranked[0]
    selected["reason"] = (
        "Highest relative visual-change and audio-peak activity among the evaluated windows. "
        "This is a signal-based suggestion, not gameplay-event recognition."
    )
    alternatives = []
    for candidate in ranked[1:]:
        if all(abs(candidate["start_time_seconds"] - item["start_time_seconds"]) >= window for item in alternatives + [selected]):
            alternatives.append(candidate)
        if len(alternatives) == 2:
            break
    selected["alternatives"] = alternatives
    selected["analysis"] = {
        "sample_rate_fps": 2,
        "audio_sample_window_seconds": 0.5,
        "weights": {"visual_change": 0.75, "audio_peak": 0.25},
        "score_interpretation": "relative rank score; not a probability",
    }
    return selected
