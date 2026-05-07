import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

import config
import db

logger = logging.getLogger(__name__)

LINKUP_V2_URL = "https://api.linkupapi.com/v2/content"
FEED_FETCH_SIZE = 50
POST_MAX_AGE_HOURS = 8
SEARCH_KEYWORDS = "AI product management"


def _make_post_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _linkup_headers() -> dict:
    return {
        "x-api-key": config.LINKUP_API_KEY,
        "Content-Type": "application/json",
    }


def _build_creator_url_index() -> dict[str, dict]:
    """Map normalised LinkedIn profile URL → creator dict + priority tier."""
    index = {}
    for tier in config.PRIORITY_ORDER:
        for creator in config.CREATORS.get(tier, []):
            normalised = creator["linkedin_url"].rstrip("/").lower()
            index[normalised] = {"creator": creator, "priority": tier}
    return index


def _match_creator(actor_url: str, index: dict) -> dict | None:
    if not actor_url:
        return None
    normalised = actor_url.split("?")[0].rstrip("/").lower()
    return index.get(normalised)


def _extract_post_time(item: dict) -> datetime | None:
    """
    Try to determine when a post was published.
    Priority: API timestamp fields → LinkedIn activity ID in URL/URN → None.
    """
    for field in ("published_at", "created_at", "created_time", "date", "timestamp"):
        val = item.get(field)
        if val:
            try:
                if isinstance(val, (int, float)):
                    ts = val / 1000 if val > 1e10 else val
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            except (ValueError, OSError):
                pass

    # Try to extract from LinkedIn activity ID (snowflake-style encoding)
    urn = item.get("urn") or item.get("id") or ""
    url = item.get("url") or item.get("post_url") or ""
    activity_id = None
    for text in [urn, url]:
        m = re.search(r"activity[:\-](\d{15,20})", text)
        if m:
            activity_id = int(m.group(1))
            break

    if activity_id:
        # LinkedIn epoch ≈ Jan 1, 2015
        LINKEDIN_EPOCH_MS = 1420070400000
        ts_ms = (activity_id >> 22) + LINKEDIN_EPOCH_MS
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            if datetime(2015, 1, 1, tzinfo=timezone.utc) < dt < now + timedelta(hours=1):
                return dt
        except (ValueError, OSError):
            pass

    return None


def _is_recent(item: dict, now: datetime) -> bool:
    """Return True if the post was published within POST_MAX_AGE_HOURS."""
    post_time = _extract_post_time(item)
    if post_time is None:
        # Can't determine age — fall back to fetched_at (treat as recent)
        logger.debug("Could not determine post time; treating as recent")
        return True
    age_hours = (now - post_time).total_seconds() / 3600
    return age_hours <= POST_MAX_AGE_HOURS


def fetch_feed() -> list[dict]:
    """Fetch the authenticated user's LinkedIn feed via LinkUp V2 API."""
    try:
        response = requests.post(
            LINKUP_V2_URL,
            headers=_linkup_headers(),
            json={
                "account_id": config.LINKUP_ACCOUNT_ID,
                "action": "get_feed",
                "params": {"total_results": FEED_FETCH_SIZE},
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error("Feed fetch failed: %s", e)
        return []

    raw = data.get("data", {}).get("Feed", [])
    if not raw and isinstance(data, list):
        raw = data
    logger.info("Feed returned %d posts (requested %d)", len(raw), FEED_FETCH_SIZE)
    for item in raw:
        actor_url = (
            item.get("actor", {}).get("linkedin_url", "")
            or item.get("actor", {}).get("profile_url", "")
            or item.get("author_url", "")
        )
        logger.debug("  Feed post author URL: %s", actor_url or "(none)")
    return raw


def fetch_search(keywords: str = SEARCH_KEYWORDS) -> list[dict]:
    """Search for recent posts via LinkUp V2 Search Posts endpoint."""
    try:
        response = requests.post(
            LINKUP_V2_URL,
            headers=_linkup_headers(),
            json={
                "account_id": config.LINKUP_ACCOUNT_ID,
                "action": "search",
                "params": {"keywords": keywords},
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error("Search fetch failed (keywords=%r): %s", keywords, e)
        return []

    raw = data.get("data", {}).get("posts", [])
    if not raw and isinstance(data, list):
        raw = data
    logger.info("Search returned %d posts (keywords=%r)", len(raw), keywords)
    return raw


def _extract_candidates(raw_posts: list[dict], creator_index: dict, source: str) -> list[dict]:
    """
    Filter a list of raw API posts to those authored by target creators
    that are recent and not yet seen. Returns structured post dicts.
    """
    now = datetime.now(timezone.utc)
    found = []
    for item in raw_posts:
        actor_url = (
            item.get("actor", {}).get("linkedin_url", "")
            or item.get("actor", {}).get("profile_url", "")
            or item.get("author_url", "")
        )
        match = _match_creator(actor_url, creator_index)
        if not match:
            continue

        if not _is_recent(item, now):
            logger.debug(
                "Skipping old post by %s (%s)",
                match["creator"]["name"],
                source,
            )
            continue

        post_url = item.get("url") or item.get("post_url") or ""
        post_text = item.get("commentary") or item.get("text") or item.get("content") or ""
        post_urn = item.get("urn") or item.get("id") or ""

        if not post_text:
            continue

        post_id = _make_post_id(post_url or post_urn or post_text[:64])
        if db.has_seen_post(post_id):
            continue

        found.append(
            {
                "post_id": post_id,
                "author": match["creator"]["name"],
                "author_headline": (
                    item.get("actor", {}).get("headline", "")
                    or item.get("actor", {}).get("subtitle", "")
                    or item.get("actor", {}).get("title", "")
                    or ""
                ),
                "post_text": post_text,
                "post_url": post_url,
                "post_urn": post_urn,
                "priority": match["priority"],
                "source": source,
            }
        )

    if found:
        author_summary = ", ".join(p["author"] for p in found)
        logger.info(
            "[%s] Found %d matching post(s): %s", source, len(found), author_summary
        )
    else:
        logger.info("[%s] No matching posts found", source)

    return found


def run_fetch() -> list[dict]:
    """
    Fetch via feed + search, filter to target creators, enforce recency,
    deduplicate, priority-order, and return up to MAX_POSTS_PER_RUN posts.
    """
    db.init_db()
    creator_index = _build_creator_url_index()

    feed_posts = fetch_feed()
    search_posts = fetch_search()

    feed_candidates = _extract_candidates(feed_posts, creator_index, "feed")
    search_candidates = _extract_candidates(search_posts, creator_index, "search")

    # Merge, deduplicate by post_id, maintain priority order
    seen_ids: set[str] = set()
    buckets: dict[str, list[dict]] = {tier: [] for tier in config.PRIORITY_ORDER}

    for post in feed_candidates + search_candidates:
        if post["post_id"] in seen_ids:
            continue
        seen_ids.add(post["post_id"])
        buckets[post["priority"]].append(post)

    candidates = []
    for tier in config.PRIORITY_ORDER:
        candidates.extend(buckets[tier])
        if len(candidates) >= config.MAX_POSTS_PER_RUN:
            break

    selected = candidates[: config.MAX_POSTS_PER_RUN]

    for post in selected:
        db.save_post(
            post["post_id"],
            post["author"],
            post["post_text"],
            post["post_url"],
            post["post_urn"],
        )
        logger.info(
            "Saved new post from %s via %s (id=%s)",
            post["author"],
            post["source"],
            post["post_id"],
        )

    return selected
