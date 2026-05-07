import hashlib
import logging
from datetime import datetime, timezone

import requests

import config
import db

logger = logging.getLogger(__name__)

LINKUP_POSTS_URL = "https://api.linkupapi.com/v1/profile/posts"


def _make_post_id(post_url: str) -> str:
    return hashlib.sha256(post_url.encode()).hexdigest()[:16]


def fetch_posts_for_creator(creator: dict) -> list[dict]:
    """Fetch recent posts for a single creator via LinkUp API."""
    try:
        response = requests.get(
            LINKUP_POSTS_URL,
            headers={"x-api-key": config.LINKUP_API_KEY},
            params={"linkedin_url": creator["linkedin_url"]},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error("Failed to fetch posts for %s: %s", creator["name"], e)
        return []

    raw_posts = data if isinstance(data, list) else data.get("posts", [])
    posts = []
    for item in raw_posts:
        post_url = item.get("url") or item.get("post_url") or ""
        post_text = item.get("text") or item.get("content") or item.get("commentary") or ""
        post_urn = item.get("urn") or item.get("post_urn") or item.get("id") or ""
        if not post_text:
            continue

        post_id = _make_post_id(post_url or post_urn or post_text[:64])
        posts.append(
            {
                "post_id": post_id,
                "author": creator["name"],
                "post_text": post_text,
                "post_url": post_url,
                "post_urn": post_urn,
                "priority": _creator_priority(creator),
            }
        )
    return posts


def _creator_priority(creator: dict) -> str:
    for tier in config.PRIORITY_ORDER:
        for c in config.CREATORS.get(tier, []):
            if c["linkedin_url"] == creator["linkedin_url"]:
                return tier
    return "medium_priority"


def run_fetch() -> list[dict]:
    """
    Fetch new posts across all creators, filtered to ones not yet seen,
    capped at MAX_POSTS_PER_RUN. Returns posts ready for comment generation.
    """
    db.init_db()
    candidates = []

    for tier in config.PRIORITY_ORDER:
        for creator in config.CREATORS.get(tier, []):
            raw = fetch_posts_for_creator(creator)
            for post in raw:
                if not db.has_seen_post(post["post_id"]):
                    candidates.append(post)

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
