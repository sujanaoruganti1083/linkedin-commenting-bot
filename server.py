import hashlib
import hmac
import json
import logging
import threading
import time

from flask import Flask, abort, jsonify, request

import config
import db
import fetcher
import generator
import poster
import scheduler
import slack_bot

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def run_pipeline():
    """Fetch posts, generate comments, and send to Slack for approval."""
    logger.info("Pipeline run started")
    db.init_db()
    posts = fetcher.run_fetch()
    if not posts:
        logger.info("No new posts found")
        return

    for post in posts:
        comments = generator.generate_comments(
            author_name=post["author"],
            post_text=post["post_text"],
            priority=post.get("priority", "medium_priority"),
        )
        if not comments:
            logger.error("Comment generation failed for post %s", post["post_id"])
            continue

        db.save_generated_comments(post["post_id"], comments)

        slack_bot.send_approval_message(
            post_author=post["author"],
            post_text=post["post_text"],
            comments=comments,
            post_url=post["post_url"],
            post_id=post["post_id"],
            author_headline=post.get("author_headline", ""),
        )
        logger.info("Sent Slack message for post %s by %s", post["post_id"], post["author"])


def _verify_slack_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp:
        logger.warning("Slack signature check failed: missing timestamp")
        return False
    try:
        age = abs(time.time() - float(timestamp))
    except ValueError:
        logger.warning("Slack signature check failed: invalid timestamp %r", timestamp)
        return False
    if age > 300:
        logger.warning("Slack signature check failed: timestamp too old (%ds)", age)
        return False
    if not raw_body:
        logger.warning("Slack signature check failed: empty request body")
        return False
    base = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        config.SLACK_SIGNING_SECRET.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    match = hmac.compare_digest(expected, signature)
    if not match:
        logger.warning("Slack signature mismatch — check SLACK_SIGNING_SECRET in .env")
    return match


def _handle_action(payload: dict):
    """Process a block_actions payload in a background thread."""
    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id = action.get("action_id", "")
    channel = payload.get("channel", {}).get("id", config.SLACK_CHANNEL_ID)
    message_ts = payload.get("message", {}).get("ts", "")
    trigger_id = payload.get("trigger_id", "")

    if action_id == "skip_post":
        post_id = action.get("value", "")
        db.save_skipped_post(post_id)
        slack_bot.update_message_skipped(channel, message_ts)
        return

    raw_value = action.get("value", "{}")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        logger.error("Could not parse action value: %s", raw_value)
        return

    post_id = value.get("post_id", "")
    archetype = value.get("archetype", "")
    comment_text = value.get("comment", "")

    if action_id.startswith("approve_"):
        post = db.get_post(post_id)
        post_url = post["post_url"] if post else ""
        success, msg = poster.post_comment(
            post_id=post_id,
            post_url=post_url,
            comment_text=comment_text,
            archetype=archetype,
            was_edited=False,
        )
        if success:
            slack_bot.update_message_posted(channel, message_ts, archetype, was_edited=False)
        else:
            slack_bot.update_message_error(channel, message_ts, msg)

    elif action_id.startswith("edit_"):
        slack_bot.open_edit_modal(trigger_id, comment_text, post_id, archetype)


def _handle_view_submission(payload: dict):
    """Process a view_submission payload (edit modal) in a background thread."""
    metadata_raw = payload.get("view", {}).get("private_metadata", "{}")
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        logger.error("Could not parse modal private_metadata: %s", metadata_raw)
        return

    post_id = metadata.get("post_id", "")
    archetype = metadata.get("archetype", "")

    values = payload.get("view", {}).get("state", {}).get("values", {})
    edited_comment = (
        values.get("comment_input", {}).get("edited_comment", {}).get("value", "")
    )

    post = db.get_post(post_id)
    if not post:
        logger.error("Post %s not found for modal submission", post_id)
        return

    success, msg = poster.post_comment(
        post_id=post_id,
        post_url=post["post_url"],
        comment_text=edited_comment,
        archetype=archetype,
        was_edited=True,
    )
    if success:
        channel = config.SLACK_CHANNEL_ID
        logger.info("Edit+post succeeded for post %s", post_id)
    else:
        logger.error("Edit+post failed for post %s: %s", post_id, msg)


@app.route("/slack/actions", methods=["POST"])
def slack_actions():
    raw_body = request.get_data(cache=True)
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(raw_body, timestamp, signature):
        abort(403)

    payload_str = request.form.get("payload", "")
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        abort(400)

    payload_type = payload.get("type")

    if payload_type == "block_actions":
        thread = threading.Thread(target=_handle_action, args=(payload,), daemon=True)
        thread.start()
        return "", 200

    if payload_type == "view_submission":
        thread = threading.Thread(
            target=_handle_view_submission, args=(payload,), daemon=True
        )
        thread.start()
        return jsonify({"response_action": "clear"})

    return "", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 5000)))
    args = parser.parse_args()

    db.init_db()
    sched = scheduler.create_scheduler(run_pipeline)
    sched.start()
    logger.info("Scheduler started. Running Flask server on port %d...", args.port)
    try:
        app.run(host="0.0.0.0", port=args.port, debug=False)
    finally:
        sched.shutdown()
