import subprocess
from pathlib import Path
from PIL import Image
from app.config import settings

def generate_image_thumbnail(source_path: Path, thumbnail_filename: str) -> bool:
    settings.thumbnails_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = settings.thumbnails_dir / thumbnail_filename
    try:
        with Image.open(source_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail((400, 400))
            img.save(thumb_path, "JPEG", quality=85)
        return True
    except Exception as e:
        print(f"Error generating image thumbnail: {e}")
        return False

def generate_video_thumbnail(source_path: Path, thumbnail_filename: str) -> bool:
    settings.thumbnails_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = settings.thumbnails_dir / thumbnail_filename
    try:
        command = [
            "ffmpeg",
            "-y",
            "-i", str(source_path),
            "-ss", "00:00:01.000",
            "-vframes", "1",
            "-vf", "scale=400:-1",
            str(thumb_path)
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception as e:
        print(f"Error generating video thumbnail: {e}")
        return False

def generate_thumbnail(file_type: str, stored_filename: str, thumbnail_filename: str):
    source_path = settings.storage_dir / stored_filename
    if file_type == "image":
        generate_image_thumbnail(source_path, thumbnail_filename)
    elif file_type == "video":
        generate_video_thumbnail(source_path, thumbnail_filename)
