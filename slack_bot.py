import json
import logging

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


def get_client() -> WebClient:
    return WebClient(token=config.SLACK_BOT_TOKEN)


def build_approval_message(
    post_author: str,
    post_snippet: str,
    comments: dict,
    post_url: str,
    post_id: str,
) -> dict:
    posted_today = db.count_posted_today()
    limit_reached = posted_today >= config.MAX_COMMENTS_PER_DAY

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"New post from {post_author}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Post preview:*\n>{post_snippet[:300]}{'...' if len(post_snippet) > 300 else ''}",
            },
        },
    ]

    if post_url:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{post_url}|View post on LinkedIn>",
                },
            }
        )

    if limit_reached:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *Daily limit reached* ({posted_today}/{config.MAX_COMMENTS_PER_DAY} posted today). "
                    "You can still review options below but posting is disabled until tomorrow.",
                },
            }
        )

    blocks.append({"type": "divider"})

    for key, label in ARCHETYPE_LABELS.items():
        comment_text = comments.get(key, "")
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{label}:*\n{comment_text}"},
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
    post_snippet: str,
    comments: dict,
    post_url: str,
    post_id: str,
) -> str | None:
    """Sends the approval message to Slack. Returns the message timestamp."""
    client = get_client()
    payload = build_approval_message(post_author, post_snippet, comments, post_url, post_id)
    try:
        result = client.chat_postMessage(channel=config.SLACK_CHANNEL_ID, **payload)
        return result["ts"]
    except SlackApiError as e:
        logger.error("Failed to send Slack message: %s", e)
        return None


def update_message_posted(
    channel: str,
    ts: str,
    archetype: str,
    was_edited: bool = False,
):
    client = get_client()
    label = ARCHETYPE_LABELS.get(archetype, archetype)
    status = "Posted (edited)" if was_edited else "Posted"
    from datetime import datetime
    posted_time = datetime.now().strftime("%I:%M %p")
    try:
        client.chat_update(
            channel=channel,
            ts=ts,
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


def open_edit_modal(trigger_id: str, comment_text: str, post_id: str, archetype: str):
    client = get_client()
    modal = {
        "type": "modal",
        "callback_id": "edit_comment_submit",
        "title": {"type": "plain_text", "text": "Edit Comment"},
        "submit": {"type": "plain_text", "text": "Post to LinkedIn"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
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
            }
        ],
        "private_metadata": json.dumps(
            {"post_id": post_id, "archetype": archetype}
        ),
    }
    try:
        client.views_open(trigger_id=trigger_id, view=modal)
    except SlackApiError as e:
        logger.error("Failed to open edit modal: %s", e)
