import os
import anthropic
import json
from pathlib import Path
from typing import Optional, Tuple

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())


def detect_product(text: str) -> Optional[str]:
    """Return 'Solvea', 'VOC.ai', or None based on keyword match."""
    text_lower = text.lower()
    scores = {}
    for product, data in CONFIG["products"].items():
        score = sum(1 for kw in data["trigger_keywords"] if kw in text_lower)
        if score > 0:
            scores[product] = score
    if not scores:
        return None
    return max(scores, key=scores.get)


def generate_reply(post_title: str, post_content: str, platform: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (reply_text, product_mentioned) or (None, None) if should skip.
    """
    product = detect_product(f"{post_title} {post_content}")
    if not product:
        return None, None

    product_desc = CONFIG["products"][product]["description"]
    style_rules = "\n".join(f"- {r}" for r in CONFIG["reply_style"]["rules"])
    max_len = (CONFIG["reply_style"]["max_length_x"] if platform == "x"
               else CONFIG["reply_style"]["max_length_reddit"])

    system_prompt = f"""You are a hands-on Amazon/Shopify seller and builder who has been selling for 5+ years.
You reply to social media posts with genuine insights from your own experience.
You sometimes mention {product} ({product_desc}) as a tool you personally use — but only when it's directly relevant.

Reply style rules:
{style_rules}

Max length: {max_len} characters for {platform}. Be concise."""

    user_prompt = f"""Post title: {post_title}

Post content:
{post_content[:800]}

Write a reply that adds real value. Mention {product} only if it fits naturally.
If it doesn't fit, reply with just: SKIP
Output only the reply text, nothing else."""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )
    reply = message.content[0].text.strip()

    if reply.upper().startswith("SKIP") or len(reply) < 20:
        return None, None

    # Trim to platform max
    if len(reply) > max_len + 50:
        reply = reply[:max_len].rsplit(" ", 1)[0] + "..."

    return reply, product


def analyze_lead(post_title: str, post_content: str, post_url: str, platform: str) -> Optional[dict]:
    """
    判断发帖人是否是 Solvea 的潜在客户，并提取关键信息。
    返回 dict 或 None（不是潜在客户）。
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    user_prompt = f"""Analyze this social media post and determine if the author is a potential customer for Solvea.

Solvea is an AI customer support agent for Shopify/ecommerce stores that:
- Autonomously handles support tickets (tracking, returns, product questions)
- Integrates directly with Shopify to take actions (process returns, update shipping)
- Provides a unified inbox for human handoff

Post URL: {post_url}
Platform: {platform}
Title: {post_title}
Content: {post_content[:600]}

Respond in JSON only:
{{
  "is_lead": true/false,
  "lead_score": 1-10,
  "pain_points": ["list of pain points mentioned"],
  "business_type": "shopify store / amazon seller / saas / other / unknown",
  "urgency": "high / medium / low",
  "reason": "one sentence why they are or aren't a lead"
}}

Only return JSON, nothing else."""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip()
        # Extract JSON
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        if not data.get("is_lead"):
            return None
        data["post_url"] = post_url
        data["platform"] = platform
        data["post_title"] = post_title
        return data
    except Exception:
        return None


def filter_team_content(post_text: str) -> str:
    """
    Decides whether a team member's post should be retweeted.
    Returns 'REPOST' or 'SKIP'.
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    persona = CONFIG.get("persona", {})
    product = CONFIG.get("product", {})

    system_prompt = f"""You are {persona.get('name', 'Bei Zhang')}, {persona.get('role', 'Growth VP at MiroMind')}.
Decide if the following tweet from a team member is worth retweeting from your account.

REPOST if the tweet is about:
- AI research, deep reasoning, or enterprise AI
- Technical insights, papers, or product progress from the team
- Anything that builds {product.get('name', 'MiroMind')}'s brand or credibility

SKIP if the tweet is:
- Personal life, casual chat, or unrelated topics
- A retweet of someone else's post with no added insight
- Duplicate of something already retweeted today

Output only the word REPOST or SKIP — nothing else."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        system=system_prompt,
        messages=[{"role": "user", "content": post_text[:600]}],
    )
    result = msg.content[0].text.strip().upper()
    return "REPOST" if result.startswith("REPOST") else "SKIP"


def score_dr_content(post_text: str) -> dict:
    """
    Evaluates Deep Research content quality and engagement potential.
    Returns {"quality": "high"|"low", "can_engage": bool}
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    system_prompt = """You evaluate tweets for Deep Research AI content quality.
High quality means: academic progress, technical discussion, industry insight, or thought leadership in AI reasoning/research.
Low quality means: hype, shallow takes, off-topic, promotional spam.

can_engage=true means there is natural space for a knowledgeable reply that adds value.

Respond ONLY with valid JSON, exactly this format:
{"quality": "high", "can_engage": true}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            system=system_prompt,
            messages=[{"role": "user", "content": post_text[:600]}],
        )
        text = msg.content[0].text.strip()
        import re as _re
        json_match = _re.search(r'\{.*\}', text, _re.DOTALL)
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
    Generates a Bei Zhang-style comment for a Deep Research post.
    Returns comment text (≤280 chars) or None if should skip.
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    persona = CONFIG.get("persona", {})
    product = CONFIG.get("product", {})

    system_prompt = f"""You are {persona.get('name', 'Bei Zhang')}, {persona.get('role', 'Growth VP at MiroMind')}.
You're building a presence in the Deep Research AI community on X.

Style: {persona.get('style', 'Insightful, technically credible, never salesy.')}

When writing a comment:
1. Lead with a genuinely valuable insight, question, or addition to the discussion
2. Only mention {product.get('name', 'MiroMind')} if it is DIRECTLY relevant to the technical point being made
3. If {product.get('name', 'MiroMind')} doesn't fit naturally, do NOT mention it — pure thought leadership is better than forced promotion
4. Keep it under 280 characters
5. Sound like a real technical person, not a marketer

If the post is not worth engaging with, reply with just: SKIP"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=system_prompt,
        messages=[{"role": "user", "content": f"Original post:\n{post_content[:600]}\n\nWrite your comment:"}],
    )
    comment = msg.content[0].text.strip()

    if comment.upper().startswith("SKIP") or len(comment) < 15:
        return None

    # Trim to 280 chars at word boundary
    if len(comment) > 280:
        comment = comment[:277].rsplit(" ", 1)[0] + "..."

    return comment
