import subprocess
import os
import json
from pathlib import Path
from gemini_orchestrator import CropCoordinates
from video_utils import get_video_info

def process_highlight(
    input_path: str,
    output_path: str,
    coords: CropCoordinates,
    cert_json_str: str
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    info = get_video_info(input_path)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    
    video_stream = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
    preserve_frame = (coords.crop_x == 0 and coords.crop_y == 0
                      and coords.crop_w == int(video_stream.get("width", 0))
                      and coords.crop_h == int(video_stream.get("height", 0)))
    vf_filter = None if preserve_frame else f"crop={coords.crop_w}:{coords.crop_h}:{coords.crop_x}:{coords.crop_y},scale=1080:1920"
    source_pixel_format = str(video_stream.get("pix_fmt", ""))
    lossless_formats = {"yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le"}
    lossless_video = preserve_frame and source_pixel_format in lossless_formats
    
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{coords.start_time:.3f}",
        "-t", f"{coords.duration:.3f}",
        "-i", str(input_path),
        *( ["-vf", vf_filter] if vf_filter else [] ),
        "-c:v", "libx264",
        "-preset", "medium",
        *( ["-qp", "0", "-pix_fmt", source_pixel_format] if lossless_video else ["-crf", "18", "-pix_fmt", "yuv420p"] )
    ]
    
    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-an"])
        
    cmd.extend([
        "-metadata", f"comment={cert_json_str}",
        "-metadata", "description=Zero-Trust Provenance Certified",
        "-movflags", "+use_metadata_tags+faststart",
        str(output_path)
    ])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg processing failed: {result.stderr}")
        
    return output_path
