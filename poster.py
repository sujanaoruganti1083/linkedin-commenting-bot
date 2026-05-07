import logging
from datetime import datetime, timedelta, timezone

import requests

import config
import db

logger = logging.getLogger(__name__)

LINKUP_V2_URL = "https://api.linkupapi.com/v2/content"
SAME_AUTHOR_COOLDOWN_HOURS = 4


def _linkup_headers() -> dict:
    return {
        "x-api-key": config.LINKUP_API_KEY,
        "Content-Type": "application/json",
    }


def check_rate_limits(author: str) -> tuple[bool, str]:
    """Returns (can_post, reason_if_not)."""
    posted_today = db.count_posted_today()
    if posted_today >= config.MAX_COMMENTS_PER_DAY:
        return False, f"Daily limit reached ({posted_today}/{config.MAX_COMMENTS_PER_DAY} posted today)"

    gap = db.seconds_since_last_post()
    if gap < config.MIN_COMMENT_GAP_SECONDS:
        wait = int(config.MIN_COMMENT_GAP_SECONDS - gap)
        return False, f"Minimum gap not met — {wait}s remaining"

    last = db.last_comment_on_author(author)
    if last:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=SAME_AUTHOR_COOLDOWN_HOURS)
        if last > cutoff:
            remaining = int((last - cutoff).total_seconds() / 60)
            return False, f"Already commented on {author} within {SAME_AUTHOR_COOLDOWN_HOURS}h — {remaining}min remaining"

    return True, ""


def post_comment(
    post_id: str,
    post_url: str,
    comment_text: str,
    archetype: str,
    was_edited: bool = False,
) -> tuple[bool, str]:
    """
    Posts a comment to LinkedIn via LinkUp V2 API.
    Returns (success, message).
    """
    post = db.get_post(post_id)
    author = post["author"] if post else "unknown"

    can_post, reason = check_rate_limits(author)
    if not can_post:
        logger.warning("Rate limit blocked post: %s", reason)
        return False, reason

    try:
        response = requests.post(
            LINKUP_V2_URL,
            headers=_linkup_headers(),
            json={
                "account_id": config.LINKUP_ACCOUNT_ID,
                "action": "comment",
                "params": {
                    "post_url": post_url,
                    "message": comment_text,
                },
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        logger.error("LinkedIn post failed (HTTP %s): %s", response.status_code, e)
        return False, f"LinkedIn API error: {response.status_code}"
    except Exception as e:
        logger.error("LinkedIn post failed: %s", e)
        return False, str(e)

    db.save_posted_comment(post_id, archetype, comment_text, was_edited)
    logger.info("Posted %s comment on post %s by %s", archetype, post_id, author)
    return True, "Posted successfully"
