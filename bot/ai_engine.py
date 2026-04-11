import os
import json
import re
from pathlib import Path
from typing import Optional, Tuple
from openai import OpenAI

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())

# OpenRouter model IDs — change here if you want different models
MODEL_FAST = "anthropic/claude-haiku-4-5"    # filtering, scoring (cheap)
MODEL_QUALITY = "anthropic/claude-sonnet-4-5" # comment generation (quality)


def _client() -> OpenAI:
    return OpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )


def _chat(system: str, user: str, model: str, max_tokens: int) -> str:
    """Single helper for all OpenRouter calls."""
    resp = _client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.choices[0].message.content.strip()


# ── Legacy functions (kept for compatibility, not used in new flows) ─────────

def detect_product(text: str) -> Optional[str]:
    """Keyword-based product detection (legacy)."""
    products = CONFIG.get("products", {})
    if not products:
        return None
    text_lower = text.lower()
    scores = {}
    for product, data in products.items():
        score = sum(1 for kw in data.get("trigger_keywords", []) if kw in text_lower)
        if score > 0:
            scores[product] = score
    return max(scores, key=scores.get) if scores else None


def generate_reply(post_title: str, post_content: str, platform: str) -> Tuple[Optional[str], Optional[str]]:
    """Legacy reply generator (not used in new flows)."""
    return None, None


def analyze_lead(post_title: str, post_content: str, post_url: str, platform: str) -> Optional[dict]:
    """Legacy lead analysis (not used in new flows)."""
    return None


# ── New flow functions ────────────────────────────────────────────────────────

def filter_team_content(post_text: str) -> str:
    """
    Flow 1: Decides whether a team member's post should be retweeted.
    Returns 'REPOST' or 'SKIP'.
    """
    persona = CONFIG.get("persona", {})
    product = CONFIG.get("product", {})

    system = f"""You are {persona.get('name', 'Bei Zhang')}, {persona.get('role', 'Growth VP at MiroMind')}.
Decide if the following tweet from a team member is worth retweeting from your account.

REPOST if the tweet is about:
- AI research, deep reasoning, or enterprise AI
- Technical insights, papers, or product progress from the team
- Anything that builds {product.get('name', 'MiroMind')}'s brand or credibility

SKIP if the tweet is:
- Personal life, casual chat, or unrelated topics
- A retweet of someone else's post with no added insight

Output only the word REPOST or SKIP — nothing else."""

    result = _chat(system, post_text[:600], MODEL_FAST, 10)
    return "REPOST" if result.upper().startswith("REPOST") else "SKIP"


def score_dr_content(post_text: str) -> dict:
    """
    Flow 2: Evaluates Deep Research content quality and engagement potential.
    Returns {"quality": "high"|"low", "can_engage": bool}
    """
    system = """You evaluate tweets for Deep Research AI content quality.
High quality: academic progress, technical discussion, industry insight, thought leadership in AI reasoning/research.
Low quality: hype, shallow takes, off-topic, promotional spam.

can_engage=true means there is natural space for a knowledgeable reply that adds value.

Respond ONLY with valid JSON, exactly this format:
{"quality": "high", "can_engage": true}"""

    try:
        text = _chat(system, post_text[:600], MODEL_FAST, 50)
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return {
                "quality": data.get("quality", "low"),
                "can_engage": bool(data.get("can_engage", False)),
            }
    except Exception:
        pass
    return {"quality": "low", "can_engage": False}


def generate_comment(post_content: str) -> Optional[str]:
    """
    Flow 3: Generates a Bei Zhang-style comment for a Deep Research post.
    Returns comment text (≤280 chars) or None if should skip.
    """
    persona = CONFIG.get("persona", {})
    product = CONFIG.get("product", {})

    system = f"""You are {persona.get('name', 'Bei Zhang')}, {persona.get('role', 'Growth VP at MiroMind')}.
You're building a presence in the Deep Research AI community on X.

Style: {persona.get('style', 'Insightful, technically credible, never salesy.')}

When writing a comment:
1. Lead with a genuinely valuable insight, question, or addition to the discussion
2. Only mention {product.get('name', 'MiroMind')} if it is DIRECTLY relevant to the technical point being made
3. If {product.get('name', 'MiroMind')} doesn't fit naturally, do NOT mention it — pure thought leadership is better than forced promotion
4. Keep it under 280 characters
5. Sound like a real technical person, not a marketer

If the post is not worth engaging with, reply with just: SKIP"""

    comment = _chat(system, f"Original post:\n{post_content[:600]}\n\nWrite your comment:", MODEL_QUALITY, 200)

    if comment.upper().startswith("SKIP") or len(comment) < 15:
        return None

    if len(comment) > 280:
        comment = comment[:277].rsplit(" ", 1)[0] + "..."

    return comment
