import asyncio
import logging
import os
import time
from asyncio.subprocess import Process
from pathlib import Path
from typing import Set
from uuid import uuid4
from zipfile import ZipFile

import aiofiles
import disnake
import httpx
from disnake.ext import commands, tasks

from adropglaxy import DropGalaxy
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


def move_files_to_root(root_dir_path):
    root_dir = Path(root_dir_path)

    def move_files(directory):
        for item in directory.iterdir():
            if item.is_file():
                new_location = root_dir / item.name
                item.rename(new_location)
            elif item.is_dir():
                move_files(item)
                item.rmdir()

    move_files(root_dir)


async def upload(inter: disnake.Interaction, dir: Path) -> None:
    if dir.is_file():
        try:
            dir = segment(dir, 24, Path("."))
            await upload(inter, dir)
        except ValueError:
            await inter.channel.send(file=disnake.File(dir))
        finally:
            await inter.channel.send(
                f"{inter.author.mention} upload completes",
                delete_after=5,
                allowed_mentions=disnake.AllowedMentions(),
            )
            return
    dir_iter = {x for x in map(lambda x: Path(x), dir.iterdir()) if x.is_file()}
    zip_files = {i for i in dir_iter if str(i).endswith(".zip")}
    if zip_files:
        zip_path = Path(str(uuid4()))
        for file in zip_files:
            z = ZipFile(file)
            z.extractall(zip_path)
            file.unlink()
            move_files_to_root(zip_path)
            await upload(inter, zip_path)
    dir_iter = dir_iter - zip_files
    to_segment = {file for file in dir_iter if file.stat().st_size / 1024**2 > 24}
    if to_segment:
        logger.info(f"{len(to_segment)} files found which are more than 25mb detected")
        for file in to_segment:
            try:
                seg_dir = segment(media=file, max_size=24, save_dir=dir)
            except Exception:
                continue
            file.unlink()
            await upload(inter, seg_dir)
    dir_iter = list(dir_iter - to_segment)
    dir_iter.sort()
    logger.info(f"Uploading {dir_iter}")
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
    """
    Download and Upload the provided links and segment the video if it is more than server upload limit

    Parameters
    ----------
    attachment : The text file containing the links to download
    """
    await inter.send("Provided Links will be uploaded soon", ephemeral=True)
    url_buff = await attachment.read()
    url_list = url_buff.decode("utf-8").split("\n")
    url_set = {x for x in url_list}
    dropgalaxy_set = {x for x in url_set if x.startswith("https://dropgalaxy")}
    url_set = url_set - dropgalaxy_set

    async with httpx.AsyncClient() as client:
        dropgalaxy_resolver = DropGalaxy(client)
        links = await dropgalaxy_resolver(list(dropgalaxy_set))

    for link in links:
        url_set.add(link)

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
    """
    Shutdown the bot
    """
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
        if inter.author.id != inter.message.interaction.author.id:  # type: ignore
            await inter.send("Recording wasn't started by you!", ephemeral=True)
            return
        await inter.response.defer()
        try:
            self.process.terminate()
        finally:
            await inter.delete_original_response()


@is_guild_or_bot_owner()
@bot.slash_command(name="record")
async def record(inter: disnake.GuildCommandInteraction, model: str):
    """
    Record the stream of the provided model

    Parameters
    ----------
    model : The model name to record
    """
    recorder = NsfwLiveCam(
        model_name=model, out_dir=Path("."), client=httpx.AsyncClient()
    )
    process = await recorder.record_stream()
    await inter.send(
        await recorder.get_thumbnail(), view=RecorderView(process, recorder)
    )
    await process.wait()
    try:
        await upload(inter, recorder.out_path)
        await inter.delete_original_response()
    except FileNotFoundError:
        await inter.edit_original_response(
            "Model Is Currenlty Offline or in Private Show"
        )


if __name__ == "__main__":
    run.start()
    bot.run(os.getenv("TOKEN"))
