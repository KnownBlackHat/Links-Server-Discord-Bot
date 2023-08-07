import asyncio
import logging
import os
import time
from asyncio.subprocess import Process
from pathlib import Path
from typing import Set
from uuid import uuid4

import aiofiles
import disnake
import httpx
from disnake.ext import commands, tasks

from NsfwLiveCam import NsfwLiveCam
from video_segmenter import segment

bot = commands.InteractionBot(intents=disnake.Intents.all())
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)

queue = asyncio.Queue()


class Adownloader:
    def __init__(
        self, urls: Set, logger: logging.Logger = logging.getLogger(__name__)
    ) -> None:
        self._downloaded = set()
        self.urls = urls
        self.logger = logger

    async def _httpx_download(
        self, url: str, dir: Path, client: httpx.AsyncClient
    ) -> None:
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async with aiofiles.open(
                    dir.joinpath(str(uuid4()) + "." + url.split(".")[-1]), mode="wb"
                ) as file:
                    async for chunk in response.aiter_bytes():
                        await file.write(chunk)
            self._downloaded.add(url)
        except httpx.HTTPStatusError as e:
            logger.critical(f"Server returned {e.response.status_code} for {url}")
        except Exception:
            logger.exception(f"Error while downloading {url}")

    async def download(self) -> Path:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None), limits=httpx.Limits(max_connections=5)
        ) as client:
            dir = Path(str(uuid4()))
            dir.mkdir()
            tasks = (
                self._httpx_download(url=url, dir=dir, client=client)
                for url in self.urls
            )
            timer_start = time.perf_counter()
            logger.info(f"Downloading {len(self.urls)} Items")
            await asyncio.gather(*tasks)
            self.logger.info(
                f"{len(self.urls)} items downloaded within {time.perf_counter() - timer_start:.2f}"
            )
            not_downloaded = self.urls - self._downloaded
            if len(not_downloaded):
                logger.info(f"Failed To Download {not_downloaded}")
        return dir


async def upload(inter: disnake.Interaction, dir: Path):
    if dir.is_file():
        try:
            dir = segment(dir, 24, Path("."))
            await upload(inter, dir)
        except ValueError:
            await inter.send(file=disnake.File(dir))
        finally:
            await inter.send(
                f"{inter.author.mention} upload completes",
                delete_after=5,
                allowed_mentions=disnake.AllowedMentions(),
            )
            return
    dir_iter = {x for x in map(lambda x: Path(x), dir.iterdir()) if x.is_file()}
    to_segment = {file for file in dir_iter if file.stat().st_size / 1024**2 > 24}
    if to_segment:
        logger.info(f"{len(to_segment)} files found which are more than 25mb detected")
        for file in to_segment:
            try:
                seg_dir = segment(media=file, max_size=24, save_dir=dir)
            except:
                continue
            file.unlink()
            await upload(inter, seg_dir)
    dir_iter = dir_iter - to_segment
    total_file = [file for file in map(lambda x: disnake.File(x), dir_iter)]
    file_grps = [total_file[i : i + 10] for i in range(0, len(total_file), 10)]
    for file_grp in file_grps:
        await inter.channel.send(files=file_grp)
    for file in dir_iter:
        file.unlink()
    dir.rmdir()


def is_guild_or_bot_owner():
    def predicate(inter):
        return (
            inter.guild is not None
            and inter.guild.owner_id == inter.author.id
            or inter.guild.owner_id == bot.owner_id
        )

    return commands.check(predicate)


@is_guild_or_bot_owner()
@bot.slash_command(name="serve", dm_permission=False)
async def serve(inter: disnake.GuildCommandInteraction, attachment: disnake.Attachment):
    if attachment.content_type != "text/plain; charset=utf-8":
        await inter.send(
            "Kindly Provide .txt file having charset=utf-8", ephemeral=True
        )
    await inter.send("Provided Links will be uploaded soon")
    url_buff = await attachment.read()
    await inter.send(attachment.content_type)
    url_list = url_buff.decode("utf-8").split("\n")
    url_set = {x for x in url_list}

    async def _dwnld():
        downloader = Adownloader(urls=url_set)
        destination = await downloader.download()

        async def _upload():
            logger.info(f"Uploading from {destination}")
            await upload(inter, destination)
            logger.info("Upload Complete")

            await inter.channel.send(
                f"{inter.author.mention} Upload Completed",
                allowed_mentions=disnake.AllowedMentions(),
                delete_after=5,
            )

        asyncio.create_task(_upload())

    await queue.put(_dwnld)


@tasks.loop()
async def run():
    if queue.empty():
        return
    _f = await queue.get()
    await _f()
    queue.task_done()


@commands.is_owner()
@bot.slash_command(name="shutdown")
async def shutdown(inter: disnake.CommandInteraction):
    await inter.send("Shutting Down!", ephemeral=True)
    exit()


class RecorderView(disnake.ui.View):
    def __init__(self, process: Process, recorder: NsfwLiveCam) -> None:
        super().__init__(timeout=None)
        self.recorder = recorder
        self.process = process

    @disnake.ui.button(label="Update Thumbnail", style=disnake.ButtonStyle.green)
    async def green(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer()
        await inter.edit_original_response(await self.recorder.get_thumbnail())

    @disnake.ui.button(label="Stop Recording", style=disnake.ButtonStyle.red)
    async def red(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        self.process.terminate()
        await asyncio.sleep(1)
        await inter.send("Recording Stopped", ephemeral=True)
        await upload(inter, self.recorder.out_path)


@bot.slash_command(name="record")
async def record(inter: disnake.GuildCommandInteraction, model: str):
    recorder = NsfwLiveCam(
        model_name=model, out_dir=Path("."), client=httpx.AsyncClient()
    )
    process = await recorder.record_stream()
    await inter.send(
        await recorder.get_thumbnail(), view=RecorderView(process, recorder)
    )


if __name__ == "__main__":
    run.start()
    bot.run(os.getenv("TOKEN"))
