# LinkedIn Comment Automation with Slack Approval

Automated LinkedIn engagement tool for Sujana. Fetches posts from target creators, generates 3 comment options via Claude, routes them to Slack for one-click approval, and posts the approved comment back to LinkedIn.

## Architecture

```
[Fetcher]  →  [Generator]  →  [Slack Bot]  →  [Poster]
   ↓               ↓               ↓               ↓
LinkUp API    Anthropic API   Slack Block Kit  LinkUp API
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in all values in .env
```

### Slack App Setup

1. Create a Slack App at api.slack.com/apps
2. Enable **Incoming Webhooks**
3. Enable **Interactivity** and set Request URL to `https://your-host/slack/actions`
4. Add OAuth scopes: `chat:write`, `channels:read`
5. Install to workspace — copy Bot Token and Signing Secret to `.env`

## Running

```bash
# Start the server (scheduled fetching + Slack interaction handler)
python server.py

# Manual CLI commands
python main.py run                              # full pipeline
python main.py fetch                            # fetch only
python main.py comment "Post text here..."
python main.py comment --url "https://www.linkedin.com/posts/..."
python main.py comment --author "Aakash Gupta" "Post text here..."
python main.py comment --tone pushback "Post text here..."
python main.py comment --short "Post text here..."
python main.py history --last 10
python main.py stats
```

## Schedule

5 runs per day (PST): 8:00 AM, 10:30 AM, 1:00 PM, 3:30 PM, 6:00 PM.
Each run fetches up to 3 new posts. Max 5 comments posted per day.

## Voice Rules

Every generated comment must pass:

- Direct and specific. No vague praise.
- Technically grounded. Engineering concepts used naturally.
- Curious, not performative. Real questions, not rhetorical.
- Confident but not arrogant.
- 3 to 5 sentences. Punchy.
- No emojis.
- No dashes or hyphens.
- Takes a position someone could disagree with.
- Does NOT use: "As someone transitioning to PM", "As an engineer", "Love this!", "Great post!", "Great insights!", "Thanks for sharing!"
- Does NOT signal job searching or career dissatisfaction.
- Rotates AI-native vocabulary: evals, guardrails, orchestration, data quality, model behavior, system tradeoffs.

## Rate Limits

| Rule | Limit |
|------|-------|
| Comments per day | 5 |
| Minimum gap between comments | 2 minutes |
| Same-author cooldown | 4 hours |
| Posts per scheduler run | 3 |

## Project Structure

```
├── main.py          # CLI entry point
├── server.py        # Flask server + scheduler
├── config.py        # Environment config
├── creators.json    # Target creator list
├── fetcher.py       # LinkedIn post fetching (LinkUp API)
├── generator.py     # Comment generation (Anthropic API)
├── slack_bot.py     # Slack message building + API calls
├── poster.py        # LinkedIn comment posting (LinkUp API)
├── db.py            # SQLite operations
├── scheduler.py     # APScheduler setup
├── requirements.txt
└── .env.example
```

Database is stored in `linkedin_bot.db` (SQLite, auto-created on first run).
