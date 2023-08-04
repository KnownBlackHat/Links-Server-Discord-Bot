import asyncio
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

import aiofiles
import disnake
import httpx
from disnake.ext import commands

from video_segmenter import segment

bot = commands.InteractionBot(intents=disnake.Intents.all())
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
download = set()


async def httpx_download(url: str, file_name: Path, client: httpx.AsyncClient):
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                return
            async with aiofiles.open(file_name, mode="wb") as file:
                async for chunk in response.aiter_bytes():
                    await file.write(chunk)
        download.add(url)
    except httpx.NetworkError:
        logger.exception(f"Error while downloading {url}")


async def upload(inter: disnake.GuildCommandInteraction, dir: Path):
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
            seg_dir.rmdir()
    dir_iter = dir_iter - to_segment
    total_file = [file for file in map(lambda x: disnake.File(x), dir_iter)]
    file_grps = [total_file[i : i + 10] for i in range(0, len(total_file), 10)]
    for file_grp in file_grps:
        await inter.channel.send(files=file_grp)
    for file in dir_iter:
        file.unlink()


def is_guild_or_bot_owner():
    def predicate(inter):
        return (
            inter.guild is not None
            and inter.guild.owner_id == inter.author.id
            or inter.guild.owner_id == bot.owner_id
        )

    return commands.check(predicate)


@commands.max_concurrency(1, per=commands.BucketType.channel, wait=True)
@bot.slash_command(name="serve", dm_permission=False)
@is_guild_or_bot_owner()
async def serve(inter: disnake.GuildCommandInteraction, attachment: disnake.Attachment):
    await inter.response.defer(ephemeral=True)
    await inter.send("Serving...")
    destination = Path(str(inter.channel.id))
    try:
        destination.mkdir()
    except FileExistsError:
        ...
    url_buff = await attachment.read()
    url_list = url_buff.decode("utf-8").split("\n")
    url_set = {x for x in url_list}
    logger.info(f"Downloading {len(url_list)} Items")

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=10)) as client:
        tasks = (
            httpx_download(
                url=url,
                file_name=destination.joinpath(
                    Path(uuid4().__str__() + "." + url.split(".")[-1])
                ),
                client=client,
            )
            for url in url_list
        )
        timer_start = time.perf_counter()
        await asyncio.gather(*tasks)
        logger.info(
            f"{len(url_set)} downloaded within {time.perf_counter() - timer_start}"
        )

    logger.info(f"Not Downloaded {url_set - download}")
    logger.info(f"Uploading from {destination}")
    await upload(inter, destination)
    logger.info("Upload Complete")
    await inter.channel.send(
        f"{inter.author.mention} Upload Completed",
        allowed_mentions=disnake.AllowedMentions(),
        delete_after=5,
    )


@commands.is_owner()
@bot.slash_command(name="shutdown")
async def shutdown(inter: disnake.CommandInteraction):
    await inter.send("Shutting Down!", ephemeral=True)
    exit()


bot.run(os.getenv("TOKEN"))
