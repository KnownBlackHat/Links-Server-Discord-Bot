import asyncio
import sys
from asyncio.subprocess import Process
from pathlib import Path
from typing import Tuple
from uuid import uuid4

import ffmpeg
import httpx


class NsfwLiveCam:
    def __init__(
        self, model_name: str, out_dir: Path, client: httpx.AsyncClient
    ) -> None:
        self.model = model_name.replace("-", ";")
        self.out_path = out_dir.joinpath(f"{self.model}_{str(uuid4())}.mp4")
        self.host = "stripchat.com"
        self.client = client
        self.stream_host = "b-hls-21.doppiocdn.com"

    async def _get_model_id(self) -> Tuple[int, int]:
        url = f"https://{self.host}/api/front/v2/models/username/{self.model.replace(';', '-')}/cam"
        resp = await self.client.get(url)
        json = resp.json()
        return json["user"]["user"]["id"], json["user"]["user"]["snapshotTimestamp"]

    async def get_thumbnail(self) -> str:
        id, timestamp = await self._get_model_id()
        return f"https://img.strpst.com/thumbs/{timestamp}/{id}_webp"

    async def record_stream(self) -> Process:
        id, _ = await self._get_model_id()
        url = f"https://{self.stream_host}/hls/{id}/{id}.m3u8"
        input_options = {
            "filename": url,
        }

        output_options = {
            "c:v": "copy",
            "c:a": "copy",
            "f": "mp4",
            "preset": "ultrafast",
            "bsf:a": "aac_adtstoasc",
        }

        ffmpeg_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *ffmpeg.input(**input_options)
            .output(self.out_path.name, **output_options)
            .get_args(),
            # stdout=asyncio.subprocess.DEVNULL,
            # stderr=asyncio.subprocess.DEVNULL,
        )
        return ffmpeg_proc


if __name__ == "__main__":

    async def main():
        recorder = NsfwLiveCam(
            model_name=sys.argv[1], out_dir=Path("."), client=httpx.AsyncClient()
        )
        proc = await recorder.record_stream()
        print("Recording started")
        await proc.wait()
        print("Completed")

    asyncio.run(main())
