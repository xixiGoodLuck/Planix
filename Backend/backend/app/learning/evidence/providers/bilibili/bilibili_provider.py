from __future__ import annotations

from datetime import UTC, datetime
from hashlib import md5, sha256
from html import unescape
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from ....contracts import VideoResource
from ..base import (
    VideoSearchHit,
    VideoSearchQuery,
    VideoSourceProviderError,
)


class BilibiliProvider:
    """Synchronous Bilibili search/metadata provider; it never creates evidence."""

    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
    SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
    VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
    _BVID = re.compile(r"^BV[0-9A-Za-z]{10}$")
    _HTML_TAG = re.compile(r"<[^>]+>")
    _MIXIN_KEY_ENC_TAB = (
        46,
        47,
        18,
        2,
        53,
        8,
        23,
        32,
        15,
        50,
        10,
        31,
        58,
        3,
        45,
        35,
        27,
        43,
        5,
        49,
        33,
        9,
        42,
        19,
        29,
        28,
        14,
        39,
        12,
        38,
        41,
        13,
        37,
        48,
        7,
        16,
        24,
        55,
        40,
        61,
        26,
        17,
        0,
        1,
        60,
        51,
        30,
        4,
        22,
        25,
        54,
        21,
        56,
        59,
        6,
        63,
        57,
        62,
        11,
        36,
        20,
        34,
        44,
        52,
    )

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.time,
    ):
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
            },
        )
        self._clock = clock
        self._mixin_key_cache: tuple[str, float] | None = None
        self._search_urls: dict[str, str] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BilibiliProvider":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def search(self, query: VideoSearchQuery) -> list[VideoSearchHit]:
        hits: list[VideoSearchHit] = []
        seen: set[str] = set()
        for keyword in query.knowledge_terms:
            remaining = query.maximum_results - len(hits)
            if remaining <= 0:
                break
            data = self._search(keyword, min(remaining, 20))
            raw_results = data.get("result")
            if not isinstance(raw_results, list):
                raise VideoSourceProviderError(
                    "Bilibili search response is missing data.result"
                )
            for raw in raw_results:
                if not isinstance(raw, dict):
                    continue
                bvid = str(raw.get("bvid") or "").strip()
                if not self._BVID.fullmatch(bvid):
                    continue
                source_url = self._validate_video_url(
                    raw.get("arcurl"),
                    bvid,
                    aid=raw.get("aid"),
                )
                if bvid in seen:
                    continue
                seen.add(bvid)
                self._search_urls[bvid] = source_url
                hits.append(
                    VideoSearchHit(
                        provider="bilibili",
                        externalId=bvid,
                        canonicalUrl=source_url,
                        title=self._clean_text(raw.get("title")),
                        durationSeconds=self._parse_search_duration(raw.get("duration")),
                    )
                )
                if len(hits) >= query.maximum_results:
                    break
        return hits

    def fetch_metadata(self, external_id: str) -> VideoResource:
        requested_bvid = self._validate_bvid(external_id)
        data = self._api_data(
            self._request_json(self.VIEW_URL, {"bvid": requested_bvid}),
            "view",
        )
        returned_bvid = self._validate_bvid(data.get("bvid"))
        if returned_bvid != requested_bvid:
            raise VideoSourceProviderError(
                "Bilibili metadata identity does not match the requested video"
            )
        duration = self._positive_int(data.get("duration"), "duration")
        title = self._clean_text(data.get("title"))
        if not title:
            raise VideoSourceProviderError("Bilibili metadata is missing title")
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        author = self._clean_text(owner.get("name"))
        canonical_url = self._search_urls.get(returned_bvid)
        if canonical_url is None:
            # The identity is returned and verified by Bilibili; no model supplies this URL.
            canonical_url = self._validate_video_url(
                f"https://www.bilibili.com/video/{returned_bvid}",
                returned_bvid,
            )
        return VideoResource(
            id=f"video-bilibili-{returned_bvid}",
            provider="bilibili",
            externalId=returned_bvid,
            canonicalUrl=canonical_url,
            title=title,
            author=author,
            language="",
            technologyVersions={},
            durationSeconds=duration,
            publishedAt=self._published_at(data.get("pubdate")),
            contentFingerprint=self._fingerprint(data),
            availability="available" if data.get("state") == 0 else "unavailable",
        )

    def _search(self, keyword: str, page_size: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "search_type": "video",
            "keyword": keyword,
            "page": 1,
            "page_size": page_size,
        }
        signed = self._sign_wbi(params)
        return self._api_data(
            self._request_json(self.SEARCH_URL, signed),
            "search",
        )

    def _sign_wbi(self, params: dict[str, Any]) -> dict[str, str]:
        clean = {
            key: "".join(char for char in str(value) if char not in "!'()*")
            for key, value in params.items()
        }
        clean["wts"] = str(int(self._clock()))
        query = urlencode(sorted(clean.items()))
        digest = md5(
            f"{query}{self._mixin_key()}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return {**clean, "w_rid": digest}

    def _mixin_key(self) -> str:
        if self._mixin_key_cache is not None:
            key, expires_at = self._mixin_key_cache
            if self._clock() < expires_at:
                return key
        payload = self._request_json(self.NAV_URL, {})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise VideoSourceProviderError(
                "Bilibili nav response is missing WBI key data"
            )
        wbi_img = data.get("wbi_img")
        if not isinstance(wbi_img, dict):
            raise VideoSourceProviderError("Bilibili nav response is missing wbi_img")
        raw_key = "".join(
            self._url_stem(wbi_img.get(name)) for name in ("img_url", "sub_url")
        )
        try:
            mixin_key = "".join(raw_key[index] for index in self._MIXIN_KEY_ENC_TAB)[:32]
        except IndexError as exc:
            raise VideoSourceProviderError("Bilibili WBI keys are malformed") from exc
        if len(mixin_key) != 32:
            raise VideoSourceProviderError("Bilibili WBI mixin key is malformed")
        self._mixin_key_cache = (mixin_key, self._clock() + 600)
        return mixin_key

    def _request_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise VideoSourceProviderError(
                f"Bilibili metadata request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict):
            raise VideoSourceProviderError("Bilibili response must be one JSON object")
        return payload

    @staticmethod
    def _api_data(payload: dict[str, Any], operation: str) -> dict[str, Any]:
        if payload.get("code") != 0:
            code = payload.get("code")
            message = str(payload.get("message") or "unknown error")[:160]
            raise VideoSourceProviderError(
                f"Bilibili {operation} failed with code {code}: {message}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise VideoSourceProviderError(
                f"Bilibili {operation} response is missing data"
            )
        return data

    @classmethod
    def _validate_video_url(
        cls,
        value: Any,
        bvid: str,
        *,
        aid: Any = None,
    ) -> str:
        if not isinstance(value, str) or not value.strip():
            raise VideoSourceProviderError(
                f"Bilibili search result {bvid} is missing its source URL"
            )
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or (parsed.hostname or "").casefold()
            not in {"bilibili.com", "www.bilibili.com"}
            or not parsed.path.startswith("/video/")
        ):
            raise VideoSourceProviderError(
                f"Bilibili search result {bvid} returned an invalid video URL"
            )
        url_identity = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        identity_matches = url_identity == bvid
        if isinstance(aid, int) and not isinstance(aid, bool):
            identity_matches = identity_matches or url_identity.casefold() == f"av{aid}"
        if not identity_matches:
            raise VideoSourceProviderError(
                f"Bilibili search result {bvid} URL identity does not match its response"
            )
        return urlunsplit(("https", parsed.netloc.casefold(), parsed.path, "", ""))

    @classmethod
    def _validate_bvid(cls, value: Any) -> str:
        bvid = str(value or "").strip()
        if not cls._BVID.fullmatch(bvid):
            raise VideoSourceProviderError("Bilibili video id must be one valid BV id")
        return bvid

    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return " ".join(
            unescape(cls._HTML_TAG.sub("", str(value or ""))).split()
        ).strip()

    @staticmethod
    def _parse_search_duration(value: Any) -> int | None:
        if value in {None, ""}:
            return None
        parts = str(value).strip().split(":")
        if not parts or any(not part.isdigit() for part in parts):
            return None
        duration = 0
        for part in parts:
            duration = duration * 60 + int(part)
        return duration if duration > 0 else None

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise VideoSourceProviderError(
                f"Bilibili metadata is missing valid {field}"
            )
        return value

    @staticmethod
    def _published_at(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return (
            datetime.fromtimestamp(value, UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _url_stem(value: Any) -> str:
        if not isinstance(value, str):
            raise VideoSourceProviderError("Bilibili WBI key URL is missing")
        filename = urlsplit(value).path.rsplit("/", 1)[-1]
        return filename.split(".", 1)[0]

    @classmethod
    def _fingerprint(cls, data: dict[str, Any]) -> str:
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        fingerprint_input = {
            "bvid": data.get("bvid"),
            "aid": data.get("aid"),
            "title": cls._clean_text(data.get("title")),
            "duration": data.get("duration"),
            "pubdate": data.get("pubdate"),
            "state": data.get("state"),
            "owner": {"mid": owner.get("mid"), "name": owner.get("name")},
            "pages": [
                {
                    "cid": item.get("cid"),
                    "page": item.get("page"),
                    "part": item.get("part"),
                    "duration": item.get("duration"),
                }
                for item in pages
                if isinstance(item, dict)
            ],
        }
        serialized = json.dumps(
            fingerprint_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"


BilibiliMetadataProvider = BilibiliProvider


__all__ = ["BilibiliMetadataProvider", "BilibiliProvider"]
