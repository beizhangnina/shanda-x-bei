"""
X/Twitter reply automation via browse CLI.
Searches for relevant posts and posts replies as @VocAiSage.
"""
import re
import time
import logging
from typing import List
from . import browser as B
from .ai_engine import generate_reply, analyze_lead
from .ai_engine import filter_team_content, score_dr_content, generate_comment
from .db import log_reply, already_replied, get_today_count, save_lead
from .db import (add_to_review_queue, get_pending_follows, mark_followed,
                 add_to_follow_queue, get_follow_stats)

logger = logging.getLogger(__name__)

LOGIN_URL  = "https://x.com/login"
SEARCH_URL = "https://x.com/search?q={query}&src=typed_query&f=live"


def _is_logged_in() -> bool:
    tree = B.snapshot()
    return "bei_zhang01" in tree or "Hunter Guo" in tree


def _login_if_needed():
    """Open X and check login. If not logged in, open login page for manual auth."""
    B.open_url("https://x.com")
    B.wait_seconds(3)
    if _is_logged_in():
        logger.info("X: already logged in")
        return True
    logger.warning("X: not logged in — opening login page")
    B.open_url(LOGIN_URL)
    # Wait up to 60s for user to log in (for first-run setup)
    for _ in range(12):
        B.wait_seconds(5)
        if _is_logged_in():
            logger.info("X: login confirmed")
            return True
    logger.error("X: login timeout")
    return False


def _search_posts(query: str) -> List[dict]:
    """Search X and return list of {snippet, time_ref} dicts."""
    url = SEARCH_URL.format(query=query.replace(" ", "+"))
    B.open_url(url)
    B.wait_seconds(3)

    posts = []
    tree = B.snapshot()

    # Find article start positions in the full tree
    article_positions = [(m.start(), m.group(1)) for m in
                         re.finditer(r'\[(\d+-\d+)\] article:', tree)]

    for i, (pos, article_ref) in enumerate(article_positions[:15]):
        # Article block ends where next top-level sibling starts
        next_pos = article_positions[i + 1][0] if i + 1 < len(article_positions) else len(tree)
        block = tree[pos:next_pos]

        # Time link: [ref] link: Mar 16  OR  link: 5h  OR  link: just now
        time_match = re.search(
            r'\[(\d+-\d+)\] link: (?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|\d+[hm]|just now)',
            block
        )
        if not time_match:
            continue

        time_ref = time_match.group(1)

        # Snippet from StaticText nodes (skip very short ones like emojis/counts)
        texts = re.findall(r'StaticText: ([^\n]{10,})', block)
        snippet = " ".join(texts[:6])[:350]

        posts.append({
            "article_ref": article_ref,
            "time_ref": time_ref,
            "snippet": snippet,
        })

    return posts


def _reply_current_page(reply_text: str) -> bool:
    """Type and submit a reply on the currently open tweet page."""
    tree = B.snapshot()

    # Find reply textbox
    boxes = re.findall(r'\[(\d+-\d+)\] textbox: Post text', tree)
    if not boxes:
        return False

    B.click(boxes[0])
    B.wait_seconds(1)

    # Type reply paragraph by paragraph
    paragraphs = reply_text.split("\n\n")
    for i, para in enumerate(paragraphs):
        safe_para = para.replace("$", "")
        B.type_text(safe_para)
        if i < len(paragraphs) - 1:
            B.press("Enter")
            B.press("Enter")

    B.wait_seconds(1)

    # Find and click reply button
    tree = B.snapshot()
    reply_btns = re.findall(r'\[(\d+-\d+)\] button: Reply', tree)
    if len(reply_btns) >= 2:
        B.click(reply_btns[-1])
        B.wait_seconds(3)
        confirm_tree = B.snapshot()
        return "Your post was sent" in confirm_tree or "post was sent" in confirm_tree.lower()

    return False


def run(config: dict) -> dict:
    """
    Main entry point. Returns summary dict.
    config: from config.json["x"]
    """
    target = config["daily_target"]
    queries = config["search_queries"]
    delay = config["min_delay_seconds"]

    summary = {"posted": 0, "failed": 0, "skipped": 0, "target": target}

    if not _login_if_needed():
        logger.error("X: cannot proceed without login")
        return summary

    today_count = get_today_count("x")
    if today_count >= target:
        logger.info(f"X: already hit target ({today_count}/{target})")
        summary["posted"] = today_count
        return summary

    for query in queries:
        if get_today_count("x") >= target:
            break

        logger.info(f"X: searching '{query}'")
        posts = _search_posts(query)

        for post in posts:
            if get_today_count("x") >= target:
                break

            snippet = post.get("snippet", "")
            if not snippet or len(snippet) < 30:
                continue

            # Build a fake URL key for dedup (we don't have URL yet)
            # We'll update after opening
            reply_text, product = generate_reply(
                post_title=query,
                post_content=snippet,
                platform="x"
            )

            if not reply_text:
                summary["skipped"] += 1
                continue

            # Open tweet, get real URL, dedup check, then reply (single open)
            if not post.get("time_ref"):
                summary["skipped"] += 1
                continue

            B.click(post["time_ref"])
            B.wait_seconds(3)
            real_url = B.get_url()

            if already_replied(real_url):
                B.press("Alt+Left")
                B.wait_seconds(2)
                summary["skipped"] += 1
                continue

            # Already on tweet page — reply directly
            success = _reply_current_page(reply_text)

            if success:
                log_reply("x", real_url, query, snippet, reply_text, product, "posted")
                summary["posted"] += 1
                logger.info(f"X: posted reply #{summary['posted']} — {real_url}")
                # Lead analysis
                lead = analyze_lead(query, snippet, real_url, "x")
                if lead:
                    save_lead(lead)
                    logger.info(f"X: 🎯 lead saved score={lead.get('lead_score')} urgency={lead.get('urgency')}")
                time.sleep(delay)
            else:
                log_reply("x", real_url, query, snippet, reply_text, product, "failed")
                summary["failed"] += 1
                logger.warning(f"X: failed to post — {real_url}")
                time.sleep(10)

    return summary


def repost_team_accounts(config: dict) -> dict:
    """
    Flow 1: Visit each team account's timeline, filter with Claude, auto-repost.
    config: the full config dict (read from config.json)
    """
    team_accounts = config["x"].get("team_accounts", [])
    max_reposts = config["x"].get("max_daily_reposts", 15)
    delay = config["x"].get("min_delay_seconds", 300)

    summary = {"reposts": 0, "skipped": 0, "errors": 0}

    if not _login_if_needed():
        logger.error("Flow1: cannot proceed without login")
        return summary

    for account in team_accounts:
        if summary["reposts"] >= max_reposts:
            break

        handle = account.lstrip("@")
        profile_url = f"https://x.com/{handle}"
        logger.info(f"Flow1: visiting {profile_url}")

        try:
            B.open_url(profile_url)
            B.wait_seconds(3)
            tree = B.snapshot()

            # Find article blocks (same pattern as _search_posts)
            article_positions = [(m.start(), m.group(1)) for m in
                                 re.finditer(r'\[(\d+-\d+)\] article:', tree)]

            for i, (pos, article_ref) in enumerate(article_positions[:5]):
                if summary["reposts"] >= max_reposts:
                    break

                next_pos = article_positions[i + 1][0] if i + 1 < len(article_positions) else len(tree)
                block = tree[pos:next_pos]

                # Extract snippet
                texts = re.findall(r'StaticText: ([^\n]{10,})', block)
                snippet = " ".join(texts[:6])[:350]
                if not snippet or len(snippet) < 30:
                    continue

                # Time ref (to click into tweet)
                time_match = re.search(
                    r'\[(\d+-\d+)\] link: (?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|\d+[hm]|just now)',
                    block
                )
                if not time_match:
                    continue
                time_ref = time_match.group(1)

                # Claude filter
                decision = filter_team_content(snippet)
                if decision != "REPOST":
                    summary["skipped"] += 1
                    continue

                # Open tweet to get URL
                B.click(time_ref)
                B.wait_seconds(3)
                real_url = B.get_url()

                if already_replied(real_url):
                    B.press("Alt+Left")
                    B.wait_seconds(2)
                    summary["skipped"] += 1
                    continue

                # Find repost button and click it
                tree2 = B.snapshot()
                repost_btns = re.findall(r'\[(\d+-\d+)\] button: Repost', tree2)
                if not repost_btns:
                    B.press("Alt+Left")
                    B.wait_seconds(2)
                    summary["errors"] += 1
                    continue

                B.click(repost_btns[0])
                B.wait_seconds(1)

                # Confirm repost in popup
                tree3 = B.snapshot()
                confirm_btns = re.findall(r'\[(\d+-\d+)\] menuitem: Repost', tree3)
                if confirm_btns:
                    B.click(confirm_btns[0])
                    B.wait_seconds(2)
                    log_reply("x", real_url, account, snippet, f"[REPOST from {account}]", None, "posted")
                    summary["reposts"] += 1
                    logger.info(f"Flow1: reposted from {account} — {real_url}")
                    time.sleep(delay)
                else:
                    summary["errors"] += 1

                B.press("Alt+Left")
                B.wait_seconds(2)

        except Exception as e:
            logger.error(f"Flow1: error on {account}: {e}")
            summary["errors"] += 1

    return summary


def search_and_repost_dr(config: dict) -> dict:
    """
    Flow 2: Search Deep Research keywords, score content, auto-repost high-quality hits.
    Returns summary dict and a list of can_engage post URLs for Flow 3.
    """
    queries = config["x"].get("search_queries", [])
    max_reposts = config["x"].get("max_daily_reposts", 15)
    delay = config["x"].get("min_delay_seconds", 300)

    summary = {"reposts": 0, "skipped": 0, "can_engage_urls": []}

    if not _login_if_needed():
        logger.error("Flow2: cannot proceed without login")
        return summary

    for query in queries:
        if summary["reposts"] >= max_reposts:
            break

        logger.info(f"Flow2: searching '{query}'")
        posts = _search_posts(query)

        for post in posts:
            snippet = post.get("snippet", "")
            if not snippet or len(snippet) < 30:
                continue

            # Score content
            score = score_dr_content(snippet)
            if score["quality"] != "high":
                summary["skipped"] += 1
                continue

            if not post.get("time_ref"):
                continue

            B.click(post["time_ref"])
            B.wait_seconds(3)
            real_url = B.get_url()

            if already_replied(real_url):
                B.press("Alt+Left")
                B.wait_seconds(2)
                summary["skipped"] += 1
                continue

            # Track engage-worthy posts for Flow 3
            if score["can_engage"]:
                summary["can_engage_urls"].append({"url": real_url, "snippet": snippet})

            if summary["reposts"] < max_reposts:
                tree = B.snapshot()
                repost_btns = re.findall(r'\[(\d+-\d+)\] button: Repost', tree)
                if repost_btns:
                    B.click(repost_btns[0])
                    B.wait_seconds(1)
                    tree2 = B.snapshot()
                    confirm_btns = re.findall(r'\[(\d+-\d+)\] menuitem: Repost', tree2)
                    if confirm_btns:
                        B.click(confirm_btns[0])
                        B.wait_seconds(2)
                        log_reply("x", real_url, query, snippet, "[DR REPOST]", None, "posted")
                        summary["reposts"] += 1
                        logger.info(f"Flow2: reposted DR content — {real_url}")
                        time.sleep(delay)

            B.press("Alt+Left")
            B.wait_seconds(2)

    return summary


def search_and_queue_comments(config: dict, can_engage_posts: list = None) -> dict:
    """
    Flow 3: Generate AI comments for engage-worthy posts, add to review_queue.
    Posts are NOT published — they wait for user approval in the dashboard.
    can_engage_posts: list of {"url": ..., "snippet": ...} from Flow 2
    """
    summary = {"queued": 0, "skipped": 0}

    if not can_engage_posts:
        # Fallback: do a fresh search if no posts passed in
        queries = config["x"].get("search_queries", [])
        can_engage_posts = []
        for query in queries[:3]:  # limit to 3 queries in fallback
            posts = _search_posts(query)
            for post in posts[:5]:
                snippet = post.get("snippet", "")
                if snippet and len(snippet) >= 30:
                    score = score_dr_content(snippet)
                    if score["can_engage"]:
                        # We need the URL — open it
                        if post.get("time_ref"):
                            B.click(post["time_ref"])
                            B.wait_seconds(3)
                            url = B.get_url()
                            can_engage_posts.append({"url": url, "snippet": snippet})
                            B.press("Alt+Left")
                            B.wait_seconds(2)

    for post_data in can_engage_posts:
        url = post_data["url"]
        snippet = post_data["snippet"]

        comment = generate_comment(snippet)
        if not comment:
            summary["skipped"] += 1
            continue

        add_to_review_queue(url, snippet, comment)
        summary["queued"] += 1
        logger.info(f"Flow3: queued comment for {url}")

    return summary


def follow_daily_batch(config: dict) -> dict:
    """
    Flow 4: Follow up to max_daily_follows accounts from the pending follow_queue.
    """
    max_follows = config["x"].get("max_daily_follows", 15)
    pending = get_pending_follows(limit=max_follows)
    summary = {"followed": 0, "failed": 0}

    if not pending:
        logger.info("Flow4: no pending follows in queue")
        return summary

    if not _login_if_needed():
        logger.error("Flow4: cannot proceed without login")
        return summary

    for item in pending:
        handle = item["handle"]
        profile_url = f"https://x.com/{handle}"

        try:
            B.open_url(profile_url)
            B.wait_seconds(3)
            tree = B.snapshot()

            # Look for Follow button (not Following)
            follow_btns = re.findall(r'\[(\d+-\d+)\] button: Follow', tree)
            if not follow_btns:
                # Already following or account doesn't exist
                mark_followed(handle)
                logger.info(f"Flow4: already following or not found — {handle}")
                continue

            B.click(follow_btns[0])
            B.wait_seconds(2)

            # Verify
            tree2 = B.snapshot()
            if "Following" in tree2 or "Unfollow" in tree2:
                mark_followed(handle)
                summary["followed"] += 1
                logger.info(f"Flow4: followed @{handle}")
            else:
                summary["failed"] += 1
                logger.warning(f"Flow4: follow may have failed — @{handle}")

            time.sleep(10)  # Be gentle between follows

        except Exception as e:
            logger.error(f"Flow4: error following {handle}: {e}")
            summary["failed"] += 1

    return summary


def maybe_enqueue_influencer(handle: str, follower_count: int, config: dict):
    """
    If an account has followers above the threshold, add to follow_queue.
    Called during DR browsing when high-follower accounts are discovered.
    """
    threshold = config["x"].get("influencer_follower_threshold", 5000)
    if follower_count >= threshold:
        add_to_follow_queue(handle, source="dr_discovered")
        logger.info(f"Flow4: enqueued influencer @{handle} ({follower_count} followers)")
