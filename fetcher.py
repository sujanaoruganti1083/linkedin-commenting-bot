import hashlib
import logging

import requests

import config
import db

logger = logging.getLogger(__name__)

LINKUP_V2_URL = "https://api.linkupapi.com/v2/content"
FEED_FETCH_SIZE = 50


def _make_post_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


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


def _linkup_headers() -> dict:
    return {
        "x-api-key": config.LINKUP_API_KEY,
        "Content-Type": "application/json",
    }


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
        logger.error("Failed to fetch LinkedIn feed: %s", e)
        return []

    raw_feed = data.get("data", {}).get("Feed", [])
    if not raw_feed and isinstance(data, list):
        raw_feed = data

    logger.info("Feed returned %d posts (requested %d)", len(raw_feed), FEED_FETCH_SIZE)
    for item in raw_feed:
        actor_url = (
            item.get("actor", {}).get("linkedin_url", "")
            or item.get("actor", {}).get("profile_url", "")
            or item.get("author_url", "")
        )
        logger.debug("  Feed post author URL: %s", actor_url or "(none)")

    return raw_feed


def run_fetch() -> list[dict]:
    """
    Fetch the LinkedIn feed, filter to target creators (priority-ordered),
    skip already-seen posts, and return up to MAX_POSTS_PER_RUN new posts.
    """
    db.init_db()
    creator_index = _build_creator_url_index()
    raw_feed = fetch_feed()

    if not raw_feed:
        logger.info("Feed returned no posts")
        return []

    buckets: dict[str, list[dict]] = {tier: [] for tier in config.PRIORITY_ORDER}

    for item in raw_feed:
        actor_url = (
            item.get("actor", {}).get("linkedin_url", "")
            or item.get("actor", {}).get("profile_url", "")
            or item.get("author_url", "")
        )
        match = _match_creator(actor_url, creator_index)
        if not match:
            continue

        post_url = item.get("url") or item.get("post_url") or ""
        post_text = item.get("commentary") or item.get("text") or item.get("content") or ""
        post_urn = item.get("urn") or item.get("id") or ""

        if not post_text:
            continue

        post_id = _make_post_id(post_url or post_urn or post_text[:64])
        if db.has_seen_post(post_id):
            continue

        buckets[match["priority"]].append(
            {
                "post_id": post_id,
                "author": match["creator"]["name"],
                "post_text": post_text,
                "post_url": post_url,
                "post_urn": post_urn,
                "priority": match["priority"],
            }
        )

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
        logger.info("Saved new post from %s (id=%s)", post["author"], post["post_id"])

    return selected
