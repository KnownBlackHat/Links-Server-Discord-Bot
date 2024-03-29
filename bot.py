import asyncio
import io
import logging
import os
import time
from asyncio.subprocess import Process
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Union
from uuid import uuid4
from zipfile import BadZipfile, ZipFile

import aioshutil
import disnake
import httpx
from disnake.ext import commands, tasks

from adropglaxy import DropGalaxy
from downloader import Adownloader
from NsfwLiveCam import NsfwLiveCam
from terabox import TeraExtractor
from video_segmenter import segment

bot = commands.Bot(command_prefix="!", intents=disnake.Intents.all())
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler("bot.log", mode="w")
console_handler = logging.StreamHandler()

file_handler.setLevel(logging.DEBUG)
console_handler.setLevel(logging.INFO)
logging.basicConfig(
    level=logging.NOTSET,
    format="%(levelname)s - %(name)s - %(filename)s - %(module)s - %(funcName)s - %(message)s",
    handlers=[console_handler, file_handler],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("video_segmenter").setLevel(logging.DEBUG)
logging.getLogger("disnake").setLevel(logging.INFO)

queue = asyncio.Queue()
PREMIUM_SERVER_ID = 1204836608952635442
PREMIUM_ROLE_ID = 1205528996222214224


class Premium_Owner(commands.CommandError):
    """Raised when Premium User isn't the owner of the current server"""


def is_guild_or_bot_owner():
    def predicate(inter):
        return (
            inter.guild is not None
            and inter.guild.owner_id == inter.author.id
            or inter.author.id == bot.owner_id
        )

    return commands.check(predicate)


def is_premium_owner():
    def predicate(inter: disnake.GuildCommandInteraction) -> bool:
        server = bot.get_guild(PREMIUM_SERVER_ID)
        uid = inter.author.id
        if not server or not PREMIUM_ROLE_ID:
            return False
        elif member := server.get_member(uid):
            if member.get_role(PREMIUM_ROLE_ID):
                if not inter.author.guild_permissions.manage_guild:
                    raise Premium_Owner
                return True
        return False

    return commands.check(predicate)  # type: ignore


def is_premium_user():
    def predicate(inter: disnake.CommandInteraction) -> bool:
        server = bot.get_guild(PREMIUM_SERVER_ID)
        if not server or not PREMIUM_ROLE_ID:
            return False
        uid = inter.author.id
        if member := server.get_member(uid):
            if member.get_role(PREMIUM_ROLE_ID):
                return True
        return False

    return commands.check(predicate)  # type: ignore


def move_files_to_root(root_dir_path):
    logger.debug(f"Moving files to {root_dir_path=}")
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


async def upload_file(
    inter: Union[disnake.Interaction, commands.Context],
    file: Path,
    max_file_size: float,
    channel: Optional[Union[disnake.TextChannel, disnake.ThreadWithMessage]] = None,
) -> None:
    try:
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor() as pool:
            dir = await loop.run_in_executor(
                pool, segment, file, max_file_size, Path(".")
            )
        await upload(inter, dir, max_file_size, channel)
    except ValueError:
        if isinstance(channel, disnake.TextChannel):
            await channel.send(file=disnake.File(file))
        else:
            await inter.channel.send(file=disnake.File(file))
    finally:
        file.unlink()


async def upload_zip_v1(
    inter: Union[disnake.Interaction, commands.Context],
    zip_files: Iterable[Path],
    max_file_size: float,
    channel: Optional[Union[disnake.TextChannel, disnake.ThreadWithMessage]] = None,
) -> None:
    zip_path = Path(str(uuid4()))
    for file in zip_files:
        try:
            z = ZipFile(file)
            z.extractall(zip_path)
            z.close()
            file.unlink()
            move_files_to_root(zip_path)
            await upload(inter, zip_path, max_file_size, channel)
        except BadZipfile:
            logger.warning("Bad Zip File")
            file.unlink()
            await aioshutil.rmtree(zip_path)


async def upload_zip(
    inter: Union[disnake.Interaction, commands.Context],
    zip_files: Iterable[Path],
    max_file_size: float,
    channel: Optional[Union[disnake.TextChannel, disnake.ThreadWithMessage]] = None,
) -> None:
    zip_path = Path(str(uuid4()))
    for file in zip_files:
        try:
            await aioshutil.unpack_archive(file, zip_path)
            file.unlink()
            move_files_to_root(zip_path)
            await upload(inter, zip_path, max_file_size, channel)
        except Exception as e:
            logger.error(f"Bad Zip File {e}")
            file.unlink()
            await aioshutil.rmtree(zip_path)


async def upload_segment(
    inter: Union[disnake.Interaction, commands.Context],
    to_segment: Union[List, Set],
    dir: Path,
    max_file_size: float,
    channel: Optional[Union[disnake.TextChannel, disnake.ThreadWithMessage]] = None,
) -> None:
    logger.info(f"{len(to_segment)} files found which are more than 25mb detected")
    for file in to_segment:
        try:
            loop = asyncio.get_running_loop()
            with ProcessPoolExecutor() as pool:
                seg_dir = await loop.run_in_executor(
                    pool, segment, file, max_file_size, dir
                )
        except Exception:
            continue
        file.unlink()
        await upload(inter, seg_dir, max_file_size, channel)


async def upload(
    inter: Union[disnake.Interaction, commands.Context],
    dir: Path,
    max_file_size: float,
    channel: Optional[Union[disnake.TextChannel, disnake.ThreadWithMessage]] = None,
) -> None:
    """Generic Function for upload"""
    logger.debug(f"Upload started {dir=} {max_file_size=}")
    if dir.is_file():
        logger.debug(f"Uploading file {dir=} {max_file_size=}")
        await upload_file(inter, dir, max_file_size, channel)
    else:
        dir_iter = {x for x in map(lambda x: Path(x), dir.iterdir()) if x.is_file()}
        zip_files = {i for i in dir_iter if str(i).endswith(".zip")}
        to_segment = {
            file for file in dir_iter if file.stat().st_size / 1024**2 > max_file_size
        }
        dir_iter = dir_iter - zip_files
        dir_iter = sorted(dir_iter - to_segment)
        total_file = [file for file in map(lambda x: disnake.File(x), dir_iter)]
        file_grps = [total_file[i : i + 8] for i in range(0, len(total_file), 8)]

        if zip_files:
            logger.debug(f"Uploading zip {zip_files=} {max_file_size=}")
            await upload_zip(inter, zip_files, max_file_size, channel)
        if to_segment:
            logger.debug(f"Uploading segment {to_segment=} {dir=} {max_file_size=}")
            await upload_segment(inter, to_segment, dir, max_file_size, channel)

        logger.debug(f"Uploading to {channel=}")
        for file_grp in file_grps:
            len_file = [x.bytes_length / 1024**2 for x in file_grp]
            try:
                logger.debug(f"Uploading {sum(len_file)}")
                if isinstance(channel, disnake.ThreadWithMessage):
                    await channel.thread.send(files=file_grp)
                elif isinstance(channel, disnake.TextChannel):
                    await channel.send(files=file_grp)
                else:
                    await inter.channel.send(files=file_grp)
            except Exception as e:
                logger.error(f"Upload Failed {e} {sum(len_file)} {len_file=}")

        await aioshutil.rmtree(dir)


async def serv(
    inter: disnake.GuildCommandInteraction,
    attachment: Union[disnake.Attachment, Path],
    channel: Optional[Union[disnake.TextChannel, disnake.ThreadWithMessage]] = None,
    sequential_upload: bool = True,
):
    logger.debug(f"Serv started {attachment=} {channel=}")
    if isinstance(attachment, Path):
        url_buff = attachment.read_text()
    else:
        await inter.send(
            "Your upload is being queued, Upload will be completed soon!",
            ephemeral=True,
        )
        url_buff = (await attachment.read()).decode("utf-8")
    url_list = url_buff.split("\n")
    url_set = {x for x in url_list if x}
    dropgalaxy_set = {x for x in url_set if x.startswith("https://dropgalaxy")}
    tera_set = {x for x in url_set if x.startswith("https://terabox")}
    url_set = url_set - dropgalaxy_set
    url_set = url_set - tera_set

    logger.debug(f"DropGalaxy Links {len(dropgalaxy_set)=}")
    if dropgalaxy_set:
        async with httpx.AsyncClient(
            limits=httpx.Limits(max_connections=10),
            timeout=httpx.Timeout(None),
        ) as client:
            dropgalaxy_resolver = DropGalaxy(client)
            links = await dropgalaxy_resolver(dropgalaxy_set)
            links = {link for link in links if link}
            logger.debug(f"Resolved DropGalaxy Links {len(links)=}")
            url_set.update(links)

    logger.debug(f"TeraBox Links {len(tera_set)=}")
    if tera_set:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None, read=None),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10),
        ) as client:
            extractor = TeraExtractor(
                tera_set,
                "Magic Browser",
                client,
            )
            data = await extractor()
            logger.info(f"Resolved TeraBox Links {len(data)=}")
            logger.critical(f"{len(extractor.failed)} TeraLinks Failed To Get Link")
            url_set.update({url.resolved_link for url in data})

    url_list = list(url_set)
    url_grp = [url_list[i : i + 100] for i in range(0, len(url_list), 100)]
    logger.info(f"Url Group {len(url_grp)=}")
    for idx, url in enumerate(url_grp, 1):
        url = set(url)

        async def _dwnld(urls: Set, final: bool = False):
            downloader = Adownloader(urls=urls)
            destination = await downloader.download()

            async def _upload():
                logger.info(f"Uploading from {destination}")
                try:
                    await upload(
                        inter,
                        destination,
                        float((inter.guild.filesize_limit / 1024**2) - 1),
                        channel=channel,
                    )
                except Exception:
                    logger.error("Upload Failed")
                    return
                logger.info(f"Upload Sequence {idx}/{len(url_grp)} Completed")
                logger.info(
                    f"Upload Completed {inter.guild_id} -> {inter.guild.name}"
                ) if final else None
                if not isinstance(attachment, Path):
                    if final:
                        try:
                            await inter.author.send(
                                f"{len(set(url_list))} Upload completed in {inter.channel.mention}",  # type: ignore
                                allowed_mentions=disnake.AllowedMentions(),
                            )
                        finally:
                            await inter.channel.send(
                                f"{inter.author.mention} {len(set(url_list))} Upload completed",
                                allowed_mentions=disnake.AllowedMentions(),
                                delete_after=5,
                            )

            if sequential_upload:
                logger.info("Doing Sequential Upload")
                await _upload()
            else:
                logger.info("Doing Concurrent Upload")
                asyncio.create_task(_upload())

        if idx == len(url_grp):
            await queue.put((_dwnld, url, True))
        else:
            await queue.put((_dwnld, url, False))


@bot.slash_command(name="serve", dm_permission=False)
@is_premium_owner()
async def serve(
    inter: disnake.GuildCommandInteraction,
    attachment: disnake.Attachment,
    channel: Optional[disnake.TextChannel] = None,
    sequential_upload: bool = True,
):
    """
    Download and Upload the provided links and segment the video if it is more than server upload limit

    Parameters
    ----------
    attachment : The text file containing the links to download
    """
    await serv(inter, attachment, channel, sequential_upload=sequential_upload)


@tasks.loop()
async def run():
    if queue.empty():
        return
    _f, parm, final = await queue.get()
    await _f(parm, final)
    queue.task_done()


@commands.is_owner()
@bot.slash_command(name="shutdown")
async def shutdown(inter: disnake.CommandInteraction) -> None:
    """
    Shutdown the bot
    """
    await inter.send("Shutting Down!", ephemeral=True)
    exit()


class RecorderView(disnake.ui.View):
    def __init__(self, process: Process, recorder: NsfwLiveCam, author_id: int) -> None:
        super().__init__(timeout=None)
        self.recorder = recorder
        self.process = process
        self.author_id = author_id

    @disnake.ui.button(label="Update Thumbnail", style=disnake.ButtonStyle.green)
    async def green(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer()
        await inter.edit_original_response(await self.recorder.get_thumbnail())

    @disnake.ui.button(label="Stop Recording", style=disnake.ButtonStyle.red)
    async def red(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if inter.author.id != self.author_id:
            await inter.send("Recording wasn't started by you!", ephemeral=True)
            return
        try:
            self.process.terminate()
        finally:
            await inter.send("Stopping Recording", ephemeral=True, delete_after=2)


async def record(
    inter: Union[disnake.GuildCommandInteraction, commands.GuildContext], model: str
):
    recorder = NsfwLiveCam(
        model_name=model, out_dir=Path("."), client=httpx.AsyncClient()
    )
    process = await recorder.record_stream()
    start = time.perf_counter()
    if isinstance(inter, disnake.ApplicationCommandInteraction):
        await inter.response.send_message(
            await recorder.get_thumbnail(),
            view=RecorderView(process, recorder, inter.author.id),
        )
        msg = await inter.original_response()
    else:
        msg = await inter.send(
            await recorder.get_thumbnail(),
            view=RecorderView(process, recorder, inter.author.id),
        )
    await process.wait()
    await inter.channel.send(
        f"Stream Duration: {(time.perf_counter() - start)/60:.2f}", delete_after=5
    )

    try:
        await upload(
            inter,
            recorder.out_path,
            float((inter.guild.filesize_limit / 1024**2) - 1),
        )
    except FileNotFoundError:
        if isinstance(inter, disnake.ApplicationCommandInteraction):
            await inter.edit_original_response(
                "Model Is Currenlty Offline or in Private Show"
            )
        else:
            await msg.edit("Model Is Currenlty Offline or in Private Show")
    except Exception:
        logger.error("Unable to upload", exc_info=True)
        await inter.channel.send(file=disnake.File(Path("bot.log")))
    else:
        await inter.channel.send(
            f"{inter.author.mention} upload completed",
            delete_after=5,
            allowed_mentions=disnake.AllowedMentions(),
        )
    finally:
        await msg.delete()


@bot.slash_command(name="record")
@is_premium_user()
async def slash_record(inter: disnake.GuildCommandInteraction, model: str):
    """
    Record the stream of the provided model

    Parameters
    ----------
    model : The model name to record
    """
    await record(inter, model)


@slash_record.autocomplete("model")
async def models_suggestions(
    inter: disnake.GuildCommandInteraction, name: str
) -> Set | Dict:
    return await NsfwLiveCam(
        model_name="", out_dir=Path("."), client=httpx.AsyncClient()
    ).get_suggestions(name)


@bot.command(name="record", aliases=["r"])
@is_premium_user()
async def pre_record(ctx: commands.GuildContext, model: str):
    """
    Record the stream of the provided model

    Parameters
    ----------
    model : The model name to record
    """
    if model[0] == "'" and model[-1] == "'":
        model = model[1:-1]
    await record(ctx, model)


@commands.is_owner()
@bot.command(name="cmd")
async def cmd(ctx: commands.GuildContext, *, args):
    out = await asyncio.subprocess.create_subprocess_shell(
        args, stdout=asyncio.subprocess.PIPE
    )
    await ctx.send(file=disnake.File(io.BytesIO(await out.stdout.read()), filename="cmd.txt"))  # type: ignore


@bot.slash_command(name="clone")
@is_premium_owner()
async def clone(inter: disnake.GuildCommandInteraction):
    """Clone Commands"""


@clone.sub_command(name="to_channel")
async def clone_to_channel(
    inter: disnake.GuildCommandInteraction, zip_file: disnake.Attachment
):
    """
    Clone the provided zip file into a Text channels

    Parameters
    ----------
    zip_file : The zip file to clone
    """
    await inter.send("Cloning Started", ephemeral=True)
    zip_bytes = await zip_file.read()
    zip = ZipFile(io.BytesIO(zip_bytes))
    zip_path = Path(str(uuid4()))
    zip.extractall(zip_path)

    async def _serv(file):
        logger.info(f"Creating thread for {file.stem}")
        channel = await inter.guild.create_text_channel(name=file.stem)
        await serv(inter, file, channel=channel)

    tasks = (_serv(file) for file in zip_path.iterdir())
    await asyncio.gather(*tasks)
    await inter.send("Cloning Completed", ephemeral=True)


@clone.sub_command(name="to_forum")
async def clone_to_forum(
    inter: disnake.GuildCommandInteraction,
    zip_file: disnake.Attachment,
    channel: disnake.ForumChannel,
):
    """
    Clone the provided zip file into a forum channel

    Parameters
    ----------
    zip_file : The zip file to clone
    channel : The forum channel to clone into
    """
    await inter.send("Cloning Started", ephemeral=True)
    zip_bytes = await zip_file.read()
    zip = ZipFile(io.BytesIO(zip_bytes))
    zip_path = Path(str(uuid4()))
    zip.extractall(zip_path)

    async def _serv(file):
        logger.info(f"Creating thread for {file.stem}")
        thread = await channel.create_thread(name=file.stem, content="_ _")
        await serv(inter, file, channel=thread)

    tasks = (_serv(file) for file in zip_path.iterdir())
    await asyncio.gather(*tasks)
    await inter.send("Cloning Completed", ephemeral=True)


@bot.event
async def on_slash_command_error(inter: disnake.CommandInteraction, error):
    if isinstance(error, commands.errors.CommandOnCooldown):
        await inter.send(f"Command is on cooldown {error.retry_after:.2f} seconds")
    elif isinstance(error, commands.errors.MissingPermissions):
        await inter.send("You don't have the required permissions to run this command")
    elif isinstance(error, commands.errors.NotOwner):
        await inter.send("You are not the owner of this bot")
    elif isinstance(error, commands.errors.MissingRequiredArgument):
        await inter.send("Missing Required Argument")
    elif isinstance(error, commands.errors.BadArgument):
        await inter.send("Bad Argument")
    elif isinstance(error, commands.errors.CommandNotFound):
        await inter.send("Command Not Found")
    elif isinstance(error, commands.errors.MissingRole):
        await inter.send("You don't have the required role to run this command")
    elif isinstance(error, Premium_Owner):
        await inter.send(
            "You don't have `manage_guild` permission to run this command. Try adding me on your server"
        )
    elif isinstance(error, commands.errors.CheckFailure):
        await inter.send(
            "Buy Premium to use this command. Check My Profile for server invite link from where you can buy premium"
        )
    else:
        await inter.send(f"Something went wrong {error}")


@bot.event
async def on_command_error(ctx, err):
    await on_slash_command_error(ctx, err)


if __name__ == "__main__":
    run.start()
    bot.run(os.getenv("TOKEN"))
