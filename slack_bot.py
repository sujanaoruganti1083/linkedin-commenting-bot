import json
import logging
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config
import db

logger = logging.getLogger(__name__)

ARCHETYPE_LABELS = {
    "engineers_lens": "The Engineer's Lens",
    "respectful_pushback": "The Respectful Pushback",
    "bridge_builder": "The Bridge Builder",
}

ARCHETYPE_NUMBERS = {
    "engineers_lens": "1",
    "respectful_pushback": "2",
    "bridge_builder": "3",
}

# Slack section blocks cap at 3000 chars; stay comfortably under
_TEXT_BLOCK_LIMIT = 2800


def get_client() -> WebClient:
    return WebClient(token=config.SLACK_BOT_TOKEN)


def _quote_text(text: str) -> str:
    """Wrap each line in a Slack blockquote."""
    return "\n".join(f">{line}" if line.strip() else ">" for line in text.splitlines())


def _text_blocks(text: str) -> list[dict]:
    """Split long text into multiple mrkdwn section blocks if needed."""
    quoted = _quote_text(text)
    chunks = []
    while len(quoted) > _TEXT_BLOCK_LIMIT:
        split_at = quoted.rfind("\n", 0, _TEXT_BLOCK_LIMIT)
        if split_at == -1:
            split_at = _TEXT_BLOCK_LIMIT
        chunks.append(quoted[:split_at])
        quoted = quoted[split_at:].lstrip("\n")
    chunks.append(quoted)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in chunks
        if chunk.strip()
    ]


def build_approval_message(
    post_author: str,
    post_text: str,
    comments: dict,
    post_url: str,
    post_id: str,
    author_headline: str = "",
) -> dict:
    posted_today = db.count_posted_today()
    limit_reached = posted_today >= config.MAX_COMMENTS_PER_DAY

    blocks: list[dict] = []

    # ── Post header ──────────────────────────────────────────────────
    blocks.append(
        {
            "type": "header",
            "text": {"type": "plain_text", "text": post_author, "emoji": False},
        }
    )

    if author_headline:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": author_headline}
                ],
            }
        )

    # ── Full post text as quote block ────────────────────────────────
    blocks.extend(_text_blocks(post_text))

    # ── Link to original ─────────────────────────────────────────────
    if post_url:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{post_url}|View on LinkedIn>"},
            }
        )

    # ── Daily limit warning ───────────────────────────────────────────
    if limit_reached:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *Daily limit reached* "
                        f"({posted_today}/{config.MAX_COMMENTS_PER_DAY} posted today). "
                        "Posting is disabled until tomorrow."
                    ),
                },
            }
        )

    blocks.append({"type": "divider"})

    # ── Comment options ───────────────────────────────────────────────
    for key, label in ARCHETYPE_LABELS.items():
        comment_text = comments.get(key, "")
        num = ARCHETYPE_NUMBERS[key]

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Option {num} — {label}*\n{comment_text}",
                },
            }
        )

        action_elements = []
        if not limit_reached:
            action_elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Post This"},
                    "style": "primary",
                    "action_id": f"approve_{key}",
                    "value": json.dumps(
                        {"post_id": post_id, "archetype": key, "comment": comment_text}
                    ),
                }
            )
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Edit First"},
                "action_id": f"edit_{key}",
                "value": json.dumps(
                    {"post_id": post_id, "archetype": key, "comment": comment_text}
                ),
            }
        )

        blocks.append({"type": "actions", "elements": action_elements})
        blocks.append({"type": "divider"})

    # ── Skip button ───────────────────────────────────────────────────
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Skip This Post"},
                    "style": "danger",
                    "action_id": "skip_post",
                    "value": post_id,
                }
            ],
        }
    )

    return {"blocks": blocks}


def send_approval_message(
    post_author: str,
    post_text: str,
    comments: dict,
    post_url: str,
    post_id: str,
    author_headline: str = "",
) -> str | None:
    """Send a self-contained approval message per post. Returns message ts."""
    client = get_client()
    payload = build_approval_message(
        post_author, post_text, comments, post_url, post_id, author_headline
    )
    try:
        result = client.chat_postMessage(
            channel=config.SLACK_CHANNEL_ID,
            text=f"New post from {post_author}",  # fallback for notifications
            **payload,
        )
        return result["ts"]
    except SlackApiError as e:
        logger.error("Failed to send Slack message: %s", e)
        return None


def update_message_posted(channel: str, ts: str, archetype: str, was_edited: bool = False):
    client = get_client()
    label = ARCHETYPE_LABELS.get(archetype, archetype)
    status = "Posted (edited)" if was_edited else "Posted"
    posted_time = datetime.now().strftime("%I:%M %p")
    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text=f"{status} — {label}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":white_check_mark: *{status}* — {label} — {posted_time}",
                    },
                }
            ],
        )
    except SlackApiError as e:
        logger.error("Failed to update Slack message: %s", e)


def update_message_skipped(channel: str, ts: str):
    client = get_client()
    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text="Skipped",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": ":no_entry_sign: *Skipped*"},
                }
            ],
        )
    except SlackApiError as e:
        logger.error("Failed to update Slack message (skip): %s", e)


def update_message_error(channel: str, ts: str, reason: str):
    client = get_client()
    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text=f"Could not post — {reason}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":x: *Could not post* — {reason}",
                    },
                }
            ],
        )
    except SlackApiError as e:
        logger.error("Failed to update Slack message (error): %s", e)


def open_edit_modal(
    trigger_id: str,
    comment_text: str,
    post_id: str,
    archetype: str,
    channel: str = "",
    message_ts: str = "",
):
    client = get_client()
    label = ARCHETYPE_LABELS.get(archetype, archetype)
    modal = {
        "type": "modal",
        "callback_id": "edit_comment_submit",
        "title": {"type": "plain_text", "text": "Edit Comment"},
        "submit": {"type": "plain_text", "text": "Post to LinkedIn"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*{label}*"}],
            },
            {
                "type": "input",
                "block_id": "comment_input",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "edited_comment",
                    "multiline": True,
                    "initial_value": comment_text,
                },
                "label": {"type": "plain_text", "text": "Your comment"},
            },
        ],
        "private_metadata": json.dumps({
            "post_id": post_id,
            "archetype": archetype,
            "channel": channel,
            "message_ts": message_ts,
        }),
    }
    try:
        client.views_open(trigger_id=trigger_id, view=modal)
    except SlackApiError as e:
        logger.error("Failed to open edit modal: %s", e)
