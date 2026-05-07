"""
CLI entry point.

Usage:
  python main.py run                              # full pipeline: fetch + generate + send to Slack
  python main.py fetch                            # fetch posts only
  python main.py comment "Post text here..."
  python main.py comment --url "https://..."
  python main.py comment --author "Name" "..."
  python main.py comment --tone pushback "..."
  python main.py comment --short "..."
  python main.py history [--last N]
  python main.py stats
"""

import argparse
import json
import sys

import db
import fetcher
import generator
import slack_bot
from server import run_pipeline


def cmd_run(_args):
    print("Running full pipeline (fetch + generate + send to Slack)...")
    run_pipeline()
    print("Done.")


def cmd_fetch(_args):
    db.init_db()
    posts = fetcher.run_fetch()
    if not posts:
        print("No new posts found.")
        return
    for post in posts:
        print(f"\n[{post['author']}] {post['post_url']}")
        print(post["post_text"][:200])


def cmd_comment(args):
    db.init_db()

    if args.url:
        import hashlib
        import requests
        import config

        resp = requests.get(
            "https://api.linkupapi.com/v1/posts/detail",
            headers={"x-api-key": config.LINKUP_API_KEY},
            params={"post_url": args.url},
            timeout=15,
        )
        if resp.ok:
            data = resp.json()
            post_text = data.get("text") or data.get("content") or ""
            author_name = args.author or data.get("author_name") or "Unknown"
        else:
            print(f"Could not fetch post from URL (HTTP {resp.status_code}). Provide text directly.")
            sys.exit(1)
    else:
        post_text = args.text or ""
        author_name = args.author or "Unknown"

    if not post_text:
        print("No post text provided.")
        sys.exit(1)

    tone_map = {
        "lens": "lens",
        "pushback": "pushback",
        "bridge": "bridge",
    }
    tone_bias = tone_map.get(args.tone) if args.tone else None

    print(f"\nGenerating comments for post by {author_name}...\n")
    comments = generator.generate_comments(
        author_name=author_name,
        post_text=post_text,
        tone_bias=tone_bias,
        short_mode=args.short,
    )

    if not comments:
        print("Comment generation failed.")
        sys.exit(1)

    labels = {
        "engineers_lens": "THE ENGINEER'S LENS",
        "respectful_pushback": "THE RESPECTFUL PUSHBACK",
        "bridge_builder": "THE BRIDGE BUILDER",
    }
    for key, label in labels.items():
        print(f"--- {label} ---")
        print(comments[key])
        print()


def cmd_history(args):
    db.init_db()
    rows = db.get_recent_history(args.last)
    if not rows:
        print("No comment history found.")
        return
    for row in rows:
        edited = " (edited)" if row["was_edited"] else ""
        print(
            f"[{row['posted_at']}] {row['author']} | {row['archetype']}{edited}\n"
            f"  {row['comment_text'][:120]}\n"
        )


def cmd_stats(_args):
    db.init_db()
    stats = db.get_stats()
    print("\nEngagement Stats")
    print("-" * 30)
    for key, val in stats.items():
        print(f"  {key.replace('_', ' ').title()}: {val}")
    print()


def main():
    parser = argparse.ArgumentParser(description="LinkedIn Comment Bot CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Full pipeline: fetch + generate + send to Slack")
    sub.add_parser("fetch", help="Fetch posts only")

    comment_p = sub.add_parser("comment", help="Generate comments for a post")
    comment_p.add_argument("text", nargs="?", default="", help="Post text")
    comment_p.add_argument("--url", default="", help="LinkedIn post URL")
    comment_p.add_argument("--author", default="", help="Author name")
    comment_p.add_argument(
        "--tone",
        choices=["lens", "pushback", "bridge"],
        help="Bias tone toward one archetype",
    )
    comment_p.add_argument("--short", action="store_true", help="2 sentences max")

    history_p = sub.add_parser("history", help="View comment history")
    history_p.add_argument("--last", type=int, default=10, help="Number of entries")

    sub.add_parser("stats", help="View engagement stats")

    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "fetch": cmd_fetch,
        "comment": cmd_comment,
        "history": cmd_history,
        "stats": cmd_stats,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
