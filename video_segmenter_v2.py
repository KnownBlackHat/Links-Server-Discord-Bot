import asyncio
import logging
from pathlib import Path
from typing import Tuple

import ffmpeg

logger = logging.getLogger(__name__)


class VidSegmenter:
    async def get_video_duration_size(self, video_path: Path) -> Tuple[float, float]:
        """Returns the duration & size of a video in Minutes & Mb."""
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            duration = float(stdout)
            return duration, video_path.stat().st_size / (1024**2)
        else:
            raise RuntimeError(
                f"Error getting video duration for {video_path}: {stderr.decode()}"
            )

    async def trim(
        self,
        media: Path,
        segment_duration: float,
        out_path: Path,
        file_name: str = "%03d.mp4",
    ):
        command = (
            ffmpeg.input(str(media))
            .output(
                str(out_path / file_name),
                codec="copy",
                map="0",
                f="segment",
                segment_time=segment_duration,
                reset_timestamps="1",
            )
            .overwrite_output()
        )

        await command.run()

    async def recheck(self, dir: Path, max_size: int):
        files = [file for file in dir.iterdir() if file.is_file()]
        for file in files:
            file_size = file.stat().st_size / (1024**2)
            segment_duration = (await self.get_video_duration_size(file))[0] / 2

            if file_size >= max_size:
                await self.trim(
                    media=file,
                    segment_duration=segment_duration,
                    out_path=dir,
                    file_name=f"{file.name}%02d.mp4",
                )
                file.unlink()

    async def segment(self, media: Path, max_size: int, save_dir: Path) -> Path:
        out_path = save_dir / media.stem
        out_path.mkdir(parents=True, exist_ok=True)

        duration, size = await self.get_video_duration_size(media)
        segment_duration = (duration / size) * max_size

        if size <= max_size:
            raise ValueError(f"Video Size is Already less than {max_size} Mb")
        elif segment_duration <= 0:
            raise ValueError("Max Size Is Too Low")

        logger.debug(f"[+] Trimming {media.name}")
        logger.debug(f"[+] Max Size: {max_size} Mb")
        logger.debug(f"[+] Duration: {duration} Minutes")
        logger.debug(f"[+] Size: {size} Mb")
        logger.debug(f"[+] Segment Duration: {segment_duration / 60} Minutes")
        logger.debug(f"[+] Saving To: {out_path.absolute()}")

        await self.trim(media, segment_duration, out_path)
        await self.recheck(out_path, max_size)

        return out_path
