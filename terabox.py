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
            raise Exception(f"{id} -> {resp.json()=}")

    async def _get_download_url(self, id_or_url: str) -> Optional[TeraLink]:
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
        resp = await self.client.post(url, headers=headers, json=data)
        resp.raise_for_status()
        if resp.status_code == 200 and resp.json().get("ok"):
            return self.TeraLink(id=id, resolved_link=resp.json().get("downloadLink"))
        else:
            raise Exception(resp.json())

    async def __call__(self) -> List[TeraLink]:
        tasks = (self._get_download_url(id) for id in self.urls if id)
        data = await asyncio.gather(*tasks)
        data = [x for x in data if x]
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
