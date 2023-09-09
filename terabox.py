import asyncio
import dataclasses
import logging
import sys
from typing import Any, Dict, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)


class FailedToGetData(Exception):
    """
    Raised when failed to get data from terabox
    """


class UnexpectedData(Exception):
    """
    Raised when Unexpected Data recieved from terabox
    """


class TeraExtractor:
    @dataclasses.dataclass
    class TeraLink:
        id: str
        resolved_link: str

    @dataclasses.dataclass
    class TeraData:
        ok: bool
        shareid: int
        uk: int
        sign: str
        timestamp: int
        list: List[Dict[str, Any]]

    def __init__(self, urls: Set, user_agent: str, client: httpx.AsyncClient) -> None:
        self.urls = urls
        self.client = client
        self.user_agent = user_agent
        self.failed = set()
        self.retry = set()

    def _get_id(self, url: str) -> str:
        return url.split("/")[-1]

    async def _sign(self, id: str) -> TeraData:
        url = f"https://terabox-dl.qtcloud.workers.dev/api/get-info?shorturl={id}"
        headers = {
            "User-Agent": self.user_agent,
            "Host": "terabox-dl.qtcloud.workers.dev",
            "Referer": "https://terabox-dl.qtcloud.workers.dev/",
        }
        resp = await self.client.get(url, headers=headers)
        resp.raise_for_status()
        try:
            return self.TeraData(**resp.json())
        except TypeError:
            if resp.json().get("message") == "Failed get data":
                raise FailedToGetData
            elif resp.json().get("message") == "Unexpected token":
                raise UnexpectedData

    async def _get_download_url(self, id_or_url: str) -> Optional[TeraLink]:
        await asyncio.sleep(0.2)
        if id_or_url.startswith("http"):
            id = self._get_id(id_or_url)
        else:
            id = id_or_url
        try:
            teradata = await self._sign(id)
        except FailedToGetData:
            logger.critical(f"Signing Failed: {id=}")
            self.failed.add(id)
            return
        except UnexpectedData:
            logger.critical("Error In Fetching Token")
            self.retry.add(id)
            return
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", 60))
                logger.warning(f"Rate Limited, Sleeping for {retry_after}")
                await asyncio.sleep(retry_after)
                await self._get_download_url(id)
                return
            else:
                raise
        except Exception:
            teradata = await self._sign(id)

        url = "https://terabox-dl.qtcloud.workers.dev/api/get-download"
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://terabox-dl.qtcloud.workers.dev",
            "Referer": "https://terabox-dl.qtcloud.workers.dev/",
            "Alt-Used": "terabox-dl.qtcloud.workers.dev",
            "Host": "terabox-dl.qtcloud.workers.dev",
            "Origin": "https://terabox-dl.qtcloud.workers.dev",
            "Referer": "https://terabox-dl.qtcloud.workers.dev/",
            "User-Agent": self.user_agent,
        }
        data = {
            "fs_id": teradata.list[0].get("fs_id"),
            "shareid": teradata.shareid,
            "sign": teradata.sign,
            "timestamp": teradata.timestamp,
            "uk": teradata.uk,
        }
        try:
            resp = await self.client.post(url, headers=headers, json=data)
            resp.raise_for_status()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", 60))
                logger.warning(f"Rate Limited, Sleeping for {retry_after}")
                await asyncio.sleep(retry_after)
                await self._get_download_url(id)
            else:
                raise

        except Exception:
            logger.critical(f"Failed to get download link: {id=}", exc_info=True)
            await self._get_download_url(id)

        else:
            if resp.status_code == 200 and resp.json().get("ok"):
                return self.TeraLink(
                    id=id, resolved_link=resp.json().get("downloadLink")
                )
            elif "Unexpected token" in resp.json().get("message", ""):
                await self._get_download_url(id)
            else:
                raise Exception(resp.json())

    async def __call__(self, urls: Optional[Set] = None) -> List[TeraLink]:
        if not urls:
            urls = self.urls
        self.retry = set()
        tasks = (self._get_download_url(id) for id in urls if id)
        data = await asyncio.gather(*tasks)
        if self.retry:
            logger.warning(f"Retrying {len(self.retry)=}")
            await self.__call__(self.retry)
        data = [x for x in data if x]
        logger.info(f"Resolved {len(data)} TeraLinks")
        return data


if __name__ == "__main__":

    async def main():
        _usage = f"Usage: {sys.argv[0]} <url1> <url2> ..."
        if len(sys.argv) > 1:
            urls = set(sys.argv[1:])
        else:
            print(_usage)
            sys.exit(1)

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(None),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10),
        )
        extractor = TeraExtractor(urls, "Magic Browser", client)
        data = await extractor()
        for url in data:
            print(url.resolved_link)

    asyncio.run(main())
