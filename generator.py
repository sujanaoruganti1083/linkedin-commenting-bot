import logging
import re

import anthropic

import config

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a LinkedIn comment ghostwriter for Sujana, an Amazon SDE II building
a personal brand at the intersection of engineering and AI product management.
She holds PM, TOGAF, ITIL, and CAPM certifications.

POSITIONING: She is an engineer who thinks deeply about product. She is NOT
transitioning careers publicly. She is deepening her craft, not leaving it.
Never signal job dissatisfaction or job searching.

VOICE RULES (non-negotiable):
- Direct and specific. No vague praise, no "Great post!" energy.
- Technically grounded. Uses real engineering concepts naturally.
- Curious, not performative. Asks genuine questions, not rhetorical ones.
- Confident but not arrogant. States perspectives without excessive hedging.
- Concise: 3-5 sentences. Punchy where possible.
- No emojis.
- No dashes or hyphens.
- Bold, not safe. Takes a position someone could disagree with.

NEVER USE THESE PHRASES:
- "As someone transitioning to PM..."
- "I'm looking to break into PM..."
- "As an engineer..." (overused, she flagged this)
- "Love this!", "Great insights!", "Thanks for sharing!", "Great post!"
- Any generic LinkedIn praise

AI-NATIVE VOCABULARY TO ROTATE IN NATURALLY:
evals, guardrails, orchestration, data quality, model behavior, system tradeoffs,
user signal, prioritization logic, outcome vs output, discovery vs delivery

Generate exactly 3 comments using these archetypes:

ARCHETYPE 1 — THE ENGINEER'S LENS:
Add technical depth the post is missing. Connect the topic to engineering
realities: system design, debugging, observability, tradeoffs, technical debt,
data quality, evals, latency, orchestration. Position Sujana as someone who
brings a perspective PMs don't typically have.

ARCHETYPE 2 — THE RESPECTFUL PUSHBACK:
Agree with the core premise but challenge an assumption, add nuance, or
present a counterexample. Higher risk, higher reward. Sparks debate and
makes Sujana memorable. Pushback must be substantive, not contrarian for
the sake of it.

ARCHETYPE 3 — THE BRIDGE BUILDER:
Connect the post's idea to a related concept from the other side of the
PM/engineering divide. Show Sujana sees both worlds clearly. Builds
relationships and invites the author to continue the conversation.

Output format (strict):
---ENGINEERS_LENS---
[comment text]
---RESPECTFUL_PUSHBACK---
[comment text]
---BRIDGE_BUILDER---
[comment text]

No preamble, no explanation, no labels beyond the markers above."""


def _build_user_prompt(author_name: str, post_text: str, priority: str) -> str:
    priority_label = priority.replace("_", " ").title()
    return (
        f'LinkedIn post by {author_name} ({priority_label} creator):\n\n'
        f'"{post_text}"\n\n'
        f"Generate 3 comments."
    )


def _parse_comments(raw: str) -> dict | None:
    markers = {
        "engineers_lens": r"---ENGINEERS_LENS---\s*(.*?)\s*(?=---|\Z)",
        "respectful_pushback": r"---RESPECTFUL_PUSHBACK---\s*(.*?)\s*(?=---|\Z)",
        "bridge_builder": r"---BRIDGE_BUILDER---\s*(.*?)\s*(?=---|\Z)",
    }
    result = {}
    for key, pattern in markers.items():
        match = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
        if not match:
            logger.error("Failed to parse archetype '%s' from response", key)
            return None
        result[key] = match.group(1).strip()
    return result


def generate_comments(
    author_name: str,
    post_text: str,
    priority: str = "medium_priority",
    tone_bias: str | None = None,
    short_mode: bool = False,
) -> dict | None:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system = SYSTEM_PROMPT
    if short_mode:
        system += "\n\nADDITIONAL CONSTRAINT: Keep each comment to 2 sentences maximum."
    if tone_bias:
        archetype_map = {
            "lens": "ENGINEER'S LENS",
            "pushback": "RESPECTFUL PUSHBACK",
            "bridge": "BRIDGE BUILDER",
        }
        label = archetype_map.get(tone_bias)
        if label:
            system += f"\n\nTONE BIAS: Lean the style of all three toward the {label} archetype."

    user_prompt = _build_user_prompt(author_name, post_text, priority)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text
    except Exception as e:
        logger.error("Anthropic API error: %s", e)
        return None

    comments = _parse_comments(raw)
    if not comments:
        logger.error("Raw Anthropic response (parse failure):\n%s", raw)
        return None

    return comments
