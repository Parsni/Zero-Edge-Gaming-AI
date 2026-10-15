import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

def get_video_info(file_path: str) -> Dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)

def get_video_dimensions(file_path: str) -> tuple[int, int]:
    info = get_video_info(file_path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    return 1920, 1080

def extract_metadata_comment(file_path: str) -> Optional[str]:
    info = get_video_info(file_path)
    format_tags = info.get("format", {}).get("tags", {})
    if "comment" in format_tags:
        return format_tags["comment"]
    if "description" in format_tags:
        return format_tags["description"]
    for stream in info.get("streams", []):
        stream_tags = stream.get("tags", {})
        if "comment" in stream_tags:
            return stream_tags["comment"]
    return None
