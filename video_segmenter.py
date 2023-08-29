#!/bin/python3

import logging
import subprocess
import sys
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def get_video_duration_size(video_path: Path) -> Tuple[float, float]:
    """Returns the duration & size of a video in Minutes & Mb."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    duration = float(result.stdout)
    return (duration, video_path.stat().st_size / (1024**2))


def recheck(dir: Path, max_size: float, retry: int = 0):
    files = [file for file in dir.iterdir() if file.is_file()]
    should_check = False
    logger.debug("----")
    for file in files:
        file_size = file.stat().st_size / (1024**2)
        if file_size > max_size:
            should_check = True
            logger.debug(f"{file_size=} {file_size - max_size}")
            mx_size = file_size - (file_size - max_size)
            mx_size -= retry
            segment_duration = media_stats(file, mx_size).segment_duration
            trim(
                media=file,
                segment_duration=segment_duration,
                out_path=dir,
                file_name=f"{file.name}%02d.mp4",
            )
            file.unlink()
    retry += 1
    if should_check:
        recheck(dir, max_size, retry)


def media_stats(media: Path, max_size: float):
    duration, size = get_video_duration_size(media)
    segment_duration = (duration / size) * max_size

    class Meta_data:
        def __init__(self):
            self.size = size
            self.duration = duration
            self.segment_duration = segment_duration

    return Meta_data()


def trim(
    media: Path,
    segment_duration: float,
    out_path: Path,
    file_name: str = "%03d.mp4",
):
    logger.debug(f"Trimming {media=} {out_path=} {segment_duration=}")
    command = [
        "ffmpeg",
        "-i",
        media,
        "-c",
        "copy",
        "-map",
        "0",
        "-segment_time",
        str(segment_duration),
        "-f",
        "segment",
        "-reset_timestamps",
        "1",
        f"{out_path.absolute()}/{file_name}",
    ]
    subprocess.run(command, capture_output=True)


def segment(media: Path, max_size: float, save_dir: Path) -> Path:
    out_path = Path(f"{save_dir}/{media.name[:-len(media.suffix)]}")

    try:
        out_path.mkdir()
    except FileExistsError:
        ...

    stats = media_stats(media, max_size)
    if stats.size <= max_size:
        out_path.rmdir()
        raise ValueError(f"Video Size is Already less than {max_size} Mb")
    elif stats.segment_duration <= 0:
        out_path.rmdir()
        raise ValueError("Max Size Is Too Low")

    logger.debug(f"[+] Trimming {media.name}")
    logger.debug(f"[+] Max Size: {max_size} Mb")
    logger.debug(f"[+] Duration: {stats.duration} Minutes")
    logger.debug(f"[+] Size: {stats.size} Mb")
    logger.debug(f"[+] Segment Duration: {stats.segment_duration / 60} Minutes")
    logger.debug(f"[+] Saving To: {out_path.absolute()}")

    trim(media, stats.segment_duration, out_path)
    recheck(out_path, max_size)

    return out_path


if __name__ == "__main__":
    segment(
        media=Path(sys.argv[1]), max_size=int(sys.argv[2]), save_dir=Path(sys.argv[3])
    )
