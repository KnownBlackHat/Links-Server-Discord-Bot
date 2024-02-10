import asyncio
import logging
import time
from pathlib import Path
from typing import Set
from urllib.parse import urlparse
from uuid import uuid4

import aiofiles
import ffmpeg
import httpx


class Adownloader:
    def __init__(
        self, urls: Set, logger: logging.Logger = logging.getLogger(__name__)
    ) -> None:
        self._downloaded = set()
        self.urls = {x.strip() for x in urls}
        self.logger = logger

    def _get_file_ext_from_url(self, url: str) -> str:
        path = urlparse(url).path
        if "." in path:
            return path.split("/")[-1][-10:]
        return f"{path.split('/')[-1]}.mp4"

    async def _httpx_download(
        self, url: str, dir: Path, client: httpx.AsyncClient
    ) -> None:
        try:
            async with client.stream(
                "GET",
                url,
                follow_redirects=True,
                headers={"User-Agent": "Magic Browser"},
            ) as response:
                file_name = dir.joinpath(
                    str(uuid4()) + "." + self._get_file_ext_from_url(url)
                )
                async with aiofiles.open(
                    file_name,
                    mode="wb",
                ) as file:
                    async for chunk in response.aiter_bytes():
                        await file.write(chunk)
                if response.status_code != 200:
                    self.logger.critical(
                        f"Server returned {response.status_code} for {url}"
                    )
                    Path(file_name).unlink()
            self._downloaded.add(url)
        except Exception:
            self.logger.exception(f"Error while downloading {url}")

    async def download_m3u8(self, url: str, dir: Path) -> None:
        self.logger.debug(f"{dir=} {url=}")
        input_options = {
            "filename": url,
        }

        output_options = {
            "c:v": "copy",
            "c:a": "copy",
            "f": "mp4",
        }

        try:
            ffmpeg_proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                *ffmpeg.input(**input_options)
                .output(str(dir.joinpath(str(uuid4()) + ".mp4")), **output_options)
                .get_args(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await ffmpeg_proc.wait()
            self._downloaded.add(url)
        except Exception:
            self.logger.exception(f"Error while downloading {url}")

    async def download(self) -> Path:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None), limits=httpx.Limits(max_connections=5)
        ) as client:
            dir = Path(str(uuid4()))
            dir.mkdir()
            m3u8_links = {
                url for url in self.urls if urlparse(url).path.endswith(".m3u8")
            }
            httpx_links = self.urls - m3u8_links
            timer_start = time.perf_counter()
            if httpx_links:
                self.logger.info(f"Downloading {len(httpx_links)} files")
                task1 = (
                    self._httpx_download(url=url, dir=dir, client=client)
                    for url in httpx_links
                )
                await asyncio.gather(*task1, return_exceptions=True)

            if m3u8_links:
                self.logger.info(f"Downloading {len(m3u8_links)} m3u8 files")
                task2 = (self.download_m3u8(url, dir) for url in m3u8_links)
                await asyncio.gather(*task2, return_exceptions=True)

            self.logger.info(
                f"{len(self.urls)} items downloaded within {time.perf_counter() - timer_start:.2f}"
            )
            not_downloaded = self.urls - self._downloaded
            if len(not_downloaded):
                self.logger.info(f"Failed To Download {not_downloaded}")
        return dir


if __name__ == "__main__":
    import sys

    downloader = Adownloader(urls={x for x in sys.argv[1:]})
    asyncio.run(downloader.download())
