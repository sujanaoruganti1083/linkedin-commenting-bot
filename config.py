import json
import os

from dotenv import load_dotenv

load_dotenv()

LINKUP_API_KEY = os.getenv("LINKUP_API_KEY", "")
LINKUP_LOGIN_TOKEN = os.getenv("LINKUP_LOGIN_TOKEN", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

LINKEDIN_PERSON_URN = os.getenv("LINKEDIN_PERSON_URN", "")

FETCH_SCHEDULE = os.getenv("FETCH_SCHEDULE", "8:00,10:30,13:00,15:30,18:00")
TIMEZONE = os.getenv("TIMEZONE", "America/Los_Angeles")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "3"))
MAX_COMMENTS_PER_DAY = int(os.getenv("MAX_COMMENTS_PER_DAY", "5"))
MIN_COMMENT_GAP_SECONDS = int(os.getenv("MIN_COMMENT_GAP_SECONDS", "120"))

_creators_path = os.path.join(os.path.dirname(__file__), "creators.json")
with open(_creators_path) as _f:
    CREATORS = json.load(_f)

PRIORITY_ORDER = ["must_engage", "high_priority", "medium_priority"]
