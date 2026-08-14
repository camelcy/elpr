from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ZoteroUnavailable(RuntimeError):
    pass


class ZoteroNotFound(ZoteroUnavailable):
    pass


@dataclass(frozen=True)
class ZoteroResponse:
    value: Any
    last_modified_version: int


class ZoteroClient:
    def __init__(self, api_url: str, timeout_seconds: float = 8.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get(self, path: str, params: dict[str, Any] | None = None) -> ZoteroResponse:
        suffix = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.api_url}/{path.lstrip('/')}{suffix}",
            headers={"Zotero-API-Version": "3", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read().decode("utf-8"))
                version = int(response.headers.get("Last-Modified-Version", "0"))
                return ZoteroResponse(value=value, last_modified_version=version)
        except HTTPError as error:
            if error.code == 404:
                raise ZoteroNotFound(f"Zotero item or endpoint not found: {path}") from error
            raise ZoteroUnavailable(f"Zotero local API unavailable: {error}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            raise ZoteroUnavailable(f"Zotero local API unavailable: {error}") from error

    def annotations(self, since: int, limit: int) -> ZoteroResponse:
        params: dict[str, Any] = {
            "itemType": "annotation",
            "limit": limit,
            "sort": "dateModified",
            "direction": "desc",
        }
        if since > 0:
            params["since"] = since
        return self.get("items", params)

    def item(self, key: str) -> dict[str, Any]:
        return self.get(f"items/{key}").value

    def deleted(self, since: int) -> ZoteroResponse:
        if since <= 0:
            return ZoteroResponse(value={"items": []}, last_modified_version=0)
        try:
            return self.get("deleted", {"since": since})
        except ZoteroNotFound:
            # Zotero's local Connector API does not expose this Web API endpoint.
            return ZoteroResponse(value={"items": []}, last_modified_version=0)
