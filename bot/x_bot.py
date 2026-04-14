"""
X/Twitter reply automation via browse CLI.
Searches for relevant posts and posts replies as @VocAiSage.
"""
import re
import time
import random
import logging
from datetime import date, datetime
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
    """Check login by URL — most reliable method."""
    url = B.get_url()
    # Logged in: we land on /home or /bei_zhang01 or any feed page
    # Logged out: redirected to /i/flow/login or /login
    if not url:
        return False
    if "login" in url or "flow" in url or "signup" in url:
        return False
    if "x.com" in url and url not in ("https://x.com/", "https://x.com"):
        return True
    # On x.com root — check if home feed loaded vs "Happening now" landing
    tree = B.snapshot()
    return "Home timeline" in tree or "bei_zhang01" in tree


def _click_and_wait_for_tweet(ref: str, prev_url: str, timeout: int = 8) -> str:
    """Click a ref and wait until URL changes to a tweet /status/ URL. Returns new URL or ''."""
    B.click(ref)
    for _ in range(timeout):
        B.wait_seconds(1)
        url = B.get_url()
        if url and "/status/" in url and url != prev_url:
            return url
    return B.get_url()


def _login_if_needed():
    """Navigate to x.com/home — if already logged in we stay, otherwise redirect to login."""
    B.open_url("https://x.com/home")
    B.wait_seconds(4)
    if _is_logged_in():
        logger.info("X: already logged in")
        return True
    logger.warning("X: not logged in — opening login page")
    B.open_url(LOGIN_URL)
    # Wait up to 90s for manual login (first-run setup only)
    for _ in range(18):
        B.wait_seconds(5)
        if _is_logged_in():
            logger.info("X: login confirmed")
            return True
    logger.error("X: login timeout")
    return False


def _parse_age_days(time_label: str) -> float:
    """Return approximate age in days from X timestamp label. Returns 999 if unparseable."""
    label = time_label.strip().lower()
    if "just now" in label:
        return 0.0
    # "12 hours ago", "12h"
    m = re.match(r'^(\d+)\s*h(?:ours?)?(?:\s+ago)?$', label)
    if m:
        return int(m.group(1)) / 24.0
    # "45 minutes ago", "45m"
    m = re.match(r'^(\d+)\s*m(?:in(?:utes?)?)?(?:\s+ago)?$', label)
    if m:
        return int(m.group(1)) / 1440.0
    # "2 days ago", "2d"
    m = re.match(r'^(\d+)\s*d(?:ays?)?(?:\s+ago)?$', label)
    if m:
        return float(m.group(1))
    # Month-day format (e.g. "Apr 8") — calculate actual age
    try:
        today = date.today()
        dt = datetime.strptime(f"{time_label.strip()} {today.year}", "%b %d %Y").date()
        if dt > today:  # Handle year boundary
            dt = dt.replace(year=today.year - 1)
        return float((today - dt).days)
    except ValueError:
        pass
    return 999.0


def _extract_engagement(block: str) -> int:
    """Sum likes + reposts + replies from an article block for engagement filtering."""
    counts = re.findall(r'button: (\d+)\s+(?:reposts?|likes?|replies)', block)
    return sum(int(c) for c in counts)


def _search_posts(query: str, max_age_days: int = 3) -> List[dict]:
    """Search X and return list of {snippet, time_ref, age_days, engagement} dicts within max_age_days."""
    url = SEARCH_URL.format(query=query.replace(" ", "+"))
    B.open_url(url)
    B.wait_seconds(3)

    posts = []
    tree = B.snapshot()

    article_positions = [(m.start(), m.group(1)) for m in
                         re.finditer(r'\[(\d+-\d+)\] article:', tree)]

    for i, (pos, article_ref) in enumerate(article_positions[:15]):
        next_pos = article_positions[i + 1][0] if i + 1 < len(article_positions) else len(tree)
        block = tree[pos:next_pos]

        # X shows time as: "12 hours ago", "45 minutes ago", "2 days ago",
        # short forms "12h"/"2d", or absolute "Apr 8"
        time_match = re.search(
            r'\[(\d+-\d+)\] link: ('
            r'just now'
            r'|\d+\s*(?:hours?|h)(?:\s+ago)?'
            r'|\d+\s*(?:minutes?|m(?:in)?)(?:\s+ago)?'
            r'|\d+\s*(?:days?|d)(?:\s+ago)?'
            r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+'
            r')',
            block
        )
        if not time_match:
            continue

        time_ref = time_match.group(1)
        time_label = time_match.group(2)
        age_days = _parse_age_days(time_label)

        if age_days > max_age_days:
            continue  # Too old

        texts = re.findall(r'StaticText: ([^\n]{10,})', block)
        snippet = " ".join(texts[:6])[:350]
        engagement = _extract_engagement(block)

        posts.append({
            "article_ref": article_ref,
            "time_ref": time_ref,
            "age_days": age_days,
            "snippet": snippet,
            "engagement": engagement,
        })

    # Sort by engagement descending — high-activity posts first
    posts.sort(key=lambda p: p["engagement"], reverse=True)
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
                B.back()
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
    Uses JS eval to get tweet URLs and timestamps — avoids accessibility ref lifetime issues.
    config: the full config dict (read from config.json)
    """
    team_accounts = config["x"].get("team_accounts", [])
    max_reposts = config["x"].get("max_daily_reposts", 15)
    max_age_days = config["x"].get("team_max_age_days", 7)
    delay = config["x"].get("min_delay_seconds", 300)

    summary = {"reposts": 0, "skipped": 0, "errors": 0}

    if not _login_if_needed():
        logger.error("Flow1: cannot proceed without login")
        return summary

    # JS that extracts tweet data from all articles on the page
    JS_GET_TWEETS = (
        "Array.from(document.querySelectorAll('article')).slice(0,10).map(a => {"
        "  const tl = a.querySelector('time')?.parentElement;"
        "  const t = a.querySelector('time');"
        "  const texts = Array.from(a.querySelectorAll('[data-testid=\"tweetText\"]'))"
        "    .map(el => el.innerText).join(' ');"
        "  return {"
        "    url: tl ? tl.href : null,"
        "    datetime: t ? t.getAttribute('datetime') : null,"
        "    alreadyRt: !!a.querySelector('[data-testid=\"unretweet\"]'),"
        "    snippet: texts.slice(0, 300)"
        "  };"
        "}).filter(x => x.url && x.datetime)"
    )

    for account in team_accounts:
        if summary["reposts"] >= max_reposts:
            break

        handle = account.lstrip("@")
        profile_url = f"https://x.com/{handle}"
        logger.info(f"Flow1: visiting {profile_url}")

        try:
            B.open_url(profile_url)
            B.wait_seconds(4)

            tweets = B.eval_js(JS_GET_TWEETS)
            if not tweets or not isinstance(tweets, list):
                logger.warning(f"Flow1: no tweets found for {handle}")
                continue

            for tweet in tweets:
                if summary["reposts"] >= max_reposts:
                    break

                tweet_url = tweet.get("url", "")
                dt_str = tweet.get("datetime", "")
                already_rt = tweet.get("alreadyRt", False)
                snippet = tweet.get("snippet", "")

                if not tweet_url or not dt_str:
                    continue

                # Calculate age from ISO datetime
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    age_days = (datetime.now(dt.tzinfo) - dt).days
                except Exception:
                    age_days = 999

                if age_days > max_age_days:
                    continue

                if already_rt:
                    summary["skipped"] += 1
                    continue

                if already_replied(tweet_url):
                    summary["skipped"] += 1
                    continue

                if not snippet or len(snippet) < 20:
                    continue

                # Claude filter — default to REPOST for team members
                decision = filter_team_content(snippet)
                if decision != "REPOST":
                    logger.info(f"Flow1: Claude SKIP — {tweet_url}")
                    summary["skipped"] += 1
                    continue

                # Navigate to tweet page so retweet button is unambiguous
                B.open_url(tweet_url)
                B.wait_seconds(4)

                # JS click is more reliable than get_box + click_xy across rapid navigations
                if not B.js_click('[data-testid="retweet"]'):
                    logger.warning(f"Flow1: no retweet button on tweet page — {tweet_url}")
                    summary["errors"] += 1
                    B.back()
                    B.wait_seconds(2)
                    continue

                B.wait_seconds(1)

                if not B.js_click('[data-testid="retweetConfirm"]'):
                    summary["errors"] += 1
                    B.press("Escape")
                    B.back()
                    B.wait_seconds(2)
                    continue

                B.wait_seconds(2)

                log_reply("x", tweet_url, account, snippet, f"[REPOST from {account}]", None, "posted")
                summary["reposts"] += 1
                logger.info(f"Flow1: reposted {account} — {tweet_url} ({age_days}d old)")
                B.back()
                B.wait_seconds(delay)

        except Exception as e:
            logger.error(f"Flow1: error on {account}: {e}")
            summary["errors"] += 1

    return summary


def search_and_repost_dr(config: dict) -> dict:
    """
    Flow 2: Search Deep Research keywords, score content, auto-repost high-quality hits.
    Uses JS eval for tweet URL extraction and js_click for reposting.
    Returns summary dict and a list of can_engage post URLs for Flow 3.
    """
    queries = config["x"].get("search_queries", [])
    max_reposts = config["x"].get("max_daily_reposts", 15)
    delay = config["x"].get("min_delay_seconds", 300)

    # JS to extract tweet URLs + engagement from search results page
    JS_SEARCH_TWEETS = (
        "Array.from(document.querySelectorAll('article')).slice(0,15).map(a => {"
        "  const tl = a.querySelector('time')?.parentElement;"
        "  const t = a.querySelector('time');"
        "  const texts = Array.from(a.querySelectorAll('[data-testid=\"tweetText\"]'))"
        "    .map(el => el.innerText).join(' ');"
        "  const metrics = a.querySelector('[role=\"group\"]')?.getAttribute('aria-label') || '';"
        "  return {"
        "    url: tl ? tl.href : null,"
        "    datetime: t ? t.getAttribute('datetime') : null,"
        "    alreadyRt: !!a.querySelector('[data-testid=\"unretweet\"]'),"
        "    snippet: texts.slice(0, 300),"
        "    metrics: metrics"
        "  };"
        "}).filter(x => x.url && x.datetime)"
    )

    summary = {"reposts": 0, "skipped": 0, "can_engage_urls": []}

    if not _login_if_needed():
        logger.error("Flow2: cannot proceed without login")
        return summary

    for query in queries:
        if summary["reposts"] >= max_reposts:
            break

        logger.info(f"Flow2: searching '{query}'")
        search_url = SEARCH_URL.format(query=query.replace(" ", "+"))
        B.open_url(search_url)
        B.wait_seconds(4)

        tweets = B.eval_js(JS_SEARCH_TWEETS)
        if not tweets or not isinstance(tweets, list):
            continue

        # Parse engagement from metrics string and sort by engagement desc
        for tweet in tweets:
            metrics = tweet.get("metrics", "")
            eng = 0
            for m in re.findall(r'(\d+)\s+(?:repost|like|replie)', metrics):
                eng += int(m)
            tweet["engagement"] = eng

        tweets.sort(key=lambda t: t.get("engagement", 0), reverse=True)

        for tweet in tweets:
            snippet = tweet.get("snippet", "")
            if not snippet or len(snippet) < 30:
                continue

            tweet_url = tweet.get("url", "")
            if not tweet_url or "/status/" not in tweet_url:
                continue

            if tweet.get("alreadyRt", False):
                summary["skipped"] += 1
                continue

            if already_replied(tweet_url):
                summary["skipped"] += 1
                continue

            # Score content with AI
            score = score_dr_content(snippet)
            logger.info(f"Flow2: scored '{snippet[:60]}...' → {score} (engagement={tweet.get('engagement', 0)})")
            if score["quality"] != "high":
                summary["skipped"] += 1
                continue

            # Track engage-worthy posts for Flow 3
            if score.get("can_engage"):
                summary["can_engage_urls"].append({"url": tweet_url, "snippet": snippet})

            # Auto-follow KOLs who promote MiroMind/MiroThinker
            tweet_author = re.search(r'x\.com/([^/]+)/status/', tweet_url)
            if tweet_author:
                author_handle = tweet_author.group(1)
                # Don't follow our own accounts
                team_handles = {a.lstrip("@").lower() for a in config["x"].get("team_accounts", [])}
                if author_handle.lower() not in team_handles:
                    add_to_follow_queue(author_handle, source="dr_kol")
                    logger.info(f"Flow2: enqueued KOL @{author_handle} for follow")

            if summary["reposts"] < max_reposts:
                # Navigate to tweet and repost via JS
                B.open_url(tweet_url)
                B.wait_seconds(4)

                if B.js_click('[data-testid="retweet"]'):
                    B.wait_seconds(1)
                    if B.js_click('[data-testid="retweetConfirm"]'):
                        B.wait_seconds(2)
                        log_reply("x", tweet_url, query, snippet, "[DR REPOST]", None, "posted")
                        summary["reposts"] += 1
                        logger.info(f"Flow2: reposted DR content — {tweet_url}")
                        time.sleep(delay)
                    else:
                        B.press("Escape")

                B.back()
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
                            B.back()
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

            # Check follow state via aria-label (most reliable selector)
            # Normal state: "Following @handle", hover state: "Unfollow @handle"
            already_following = B.js_exists(
                f'[aria-label="Following @{handle}"], [aria-label="Unfollow @{handle}"]'
            )
            can_follow = B.js_exists(f'[aria-label="Follow @{handle}"]')

            if already_following:
                mark_followed(handle)
                logger.info(f"Flow4: already following — @{handle}")
                continue

            if not can_follow:
                mark_followed(handle)
                logger.info(f"Flow4: no follow button (suspended/deactivated?) — @{handle}")
                continue

            # Click follow via JS using handle-specific selector
            if not B.js_click(f'[aria-label="Follow @{handle}"]'):
                summary["failed"] += 1
                logger.warning(f"Flow4: js_click failed for follow — @{handle}")
                continue
            B.wait_seconds(2)

            # Verify via JS — after following, button shows "Following @handle"
            verified = B.js_exists(
                f'[aria-label="Following @{handle}"], [aria-label="Unfollow @{handle}"]'
            )
            if verified:
                mark_followed(handle)
                summary["followed"] += 1
                logger.info(f"Flow4: followed @{handle}")
            else:
                summary["failed"] += 1
                logger.warning(f"Flow4: follow may have failed — @{handle}")

            time.sleep(30 + random.randint(0, 30))  # 30-60s between follows

        except Exception as e:
            logger.error(f"Flow4: error following {handle}: {e}")
            summary["failed"] += 1

    return summary


def reply_to_engage_posts(config: dict, can_engage_posts: list = None) -> dict:
    """
    Flow 5: Reply to high-engagement posts with insightful comments.
    Uses longer delays (10-15 min) and low daily cap (5) to avoid detection.
    can_engage_posts: list of {"url": ..., "snippet": ...} from Flow 2, sorted by engagement.
    """
    max_replies = config["x"].get("max_daily_replies", 5)
    base_delay = config["x"].get("min_reply_delay_seconds", 600)
    jitter_max = config["x"].get("reply_jitter_seconds", 300)

    summary = {"replied": 0, "skipped": 0, "failed": 0}

    if not can_engage_posts:
        logger.info("Flow5: no engage-worthy posts provided — skipping")
        return summary

    if not _login_if_needed():
        logger.error("Flow5: cannot proceed without login")
        return summary

    for post_data in can_engage_posts:
        if summary["replied"] >= max_replies:
            break

        url = post_data["url"]
        snippet = post_data["snippet"]

        # Dedup — skip posts we already replied to or reposted
        if already_replied(url):
            summary["skipped"] += 1
            continue

        # Generate comment via Claude Sonnet (higher quality for replies)
        comment = generate_comment(snippet)
        if not comment:
            summary["skipped"] += 1
            continue

        logger.info(f"Flow5: replying to {url}")
        logger.info(f"Flow5: comment: {comment[:100]}...")

        try:
            B.open_url(url)
            B.wait_seconds(5)

            # Focus reply textbox
            if not B.js_click('[data-testid="tweetTextarea_0"]'):
                logger.warning(f"Flow5: can't focus reply textbox — {url}")
                summary["failed"] += 1
                continue

            B.wait_seconds(1)

            # Type reply — paragraph by paragraph for natural formatting
            paragraphs = comment.split("\n\n")
            for i, para in enumerate(paragraphs):
                safe_para = para.replace("$", "")
                B.type_text(safe_para)
                if i < len(paragraphs) - 1:
                    B.press("Enter")
                    B.press("Enter")

            B.wait_seconds(1)

            # Click Reply button
            if not B.js_click('[data-testid="tweetButtonInline"]'):
                logger.warning(f"Flow5: can't click Reply button — {url}")
                summary["failed"] += 1
                # Clear typed text
                B.press("Cmd+a")
                B.press("Backspace")
                continue

            B.wait_seconds(3)

            # Verify: check if "Your post was sent" toast appeared or textbox is cleared
            textbox_text = B.eval_js(
                "document.querySelector('[data-testid=\"tweetTextarea_0\"]')?.innerText?.trim() || ''"
            )
            # Success if textbox is empty (reply was sent and textbox cleared)
            if not textbox_text or len(textbox_text) < 5:
                log_reply("x", url, "engage_reply", snippet, comment, None, "posted")
                summary["replied"] += 1
                logger.info(f"Flow5: replied #{summary['replied']} — {url}")
            else:
                log_reply("x", url, "engage_reply", snippet, comment, None, "failed")
                summary["failed"] += 1
                logger.warning(f"Flow5: reply may have failed — {url}")

            # Long delay with random jitter to look human
            delay = base_delay + random.randint(0, jitter_max)
            logger.info(f"Flow5: waiting {delay}s before next reply")
            time.sleep(delay)

        except Exception as e:
            logger.error(f"Flow5: error replying to {url}: {e}")
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
