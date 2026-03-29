from __future__ import annotations

import calendar
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import feedparser
import httpx

from bilradio.config import MIN_DURATION_SEC, MIN_PUBDATE_UTC, RSS_URL


@dataclass
class RssEpisode:
    guid: str
    title: str
    pub_date: datetime
    enclosure_url: str
    duration_sec: int | None


def _struct_to_utc_dt(st: time.struct_time) -> datetime:
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)


def parse_itunes_duration(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:
        parts = [int(p) for p in s.split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fetch_all_entries(rss_url: str = RSS_URL) -> list[dict[str, Any]]:
    """Fetch RSS; follow Atom rel=next when the feed is paginated."""
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    url: str | None = rss_url
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        while url and url not in seen_urls:
            seen_urls.add(url)
            r = client.get(url)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            entries.extend(feed.entries)
            url = None
            for link in feed.feed.get("links", []):
                if link.get("rel") == "next" and link.get("href"):
                    url = link["href"]
                    break
    return entries


def entries_to_episodes(entries: list[dict[str, Any]]) -> list[RssEpisode]:
    out: list[RssEpisode] = []
    for e in entries:
        raw_guid = e.get("id") or e.get("guid")
        if isinstance(raw_guid, dict):
            raw_guid = raw_guid.get("value") or raw_guid.get("guid")
        guid = str(raw_guid or "").strip()
        if not guid:
            continue
        title = (e.get("title") or "").strip() or "(no title)"
        pub_struct = e.get("published_parsed") or e.get("updated_parsed")
        if not pub_struct:
            continue
        pub_date = _struct_to_utc_dt(pub_struct)
        if pub_date < MIN_PUBDATE_UTC:
            continue
        enc = None
        for l in e.get("links", []):
            if l.get("rel") == "enclosure" and l.get("href"):
                enc = l["href"]
                break
        if not enc and e.get("enclosures"):
            enc = e["enclosures"][0].get("href")
        if not enc:
            continue
        duration_sec = parse_itunes_duration(e.get("itunes_duration"))
        if duration_sec is not None and MIN_DURATION_SEC > 0 and duration_sec < MIN_DURATION_SEC:
            continue
        out.append(
            RssEpisode(
                guid=guid,
                title=title,
                pub_date=pub_date,
                enclosure_url=enc,
                duration_sec=duration_sec,
            )
        )
    return out


def load_filtered_episodes(rss_url: str = RSS_URL) -> list[RssEpisode]:
    entries = fetch_all_entries(rss_url)
    return entries_to_episodes(entries)
