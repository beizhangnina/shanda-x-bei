#!/usr/bin/env python3
"""
bei-x Daily Bot for @bei_zhang01.
Scheduled via macOS LaunchAgent at 10:05 AM.

Usage:
    python run_daily.py              # run all 4 flows
    python run_daily.py --flow 1     # team repost only
    python run_daily.py --flow 2     # DR search + repost only
    python run_daily.py --flow 3     # comment queue only
    python run_daily.py --flow 4     # follow batch only
"""
import sys
import json
import logging
import time
import random
import argparse
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"run_{datetime.now():%Y-%m-%d}.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("main")

sys.path.insert(0, str(Path(__file__).parent))
from bot.db import init_db, import_follow_list
from bot import x_bot

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())


def import_follow_list_if_needed():
    """On first run, bulk-import follow_list.txt into follow_queue."""
    follow_file = Path(__file__).parent / "follow_list.txt"
    if not follow_file.exists():
        logger.warning("follow_list.txt not found — skipping import")
        return
    handles = [line.strip() for line in follow_file.read_text().splitlines() if line.strip()]
    import_follow_list(handles)
    logger.info(f"Follow list: imported {len(handles)} handles (duplicates ignored)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", type=int, choices=[1, 2, 3, 4, 5, 6],
                        help="Run a specific flow only (1=team repost, 2=DR repost, 3=comment queue, 4=follow, 5=reply, 6=feed engage)")
    parser.add_argument("--no-jitter", action="store_true",
                        help="Skip startup jitter (for testing)")
    parser.add_argument("--skip-follow", action="store_true",
                        help="Skip Flow 4 (follow batch) — useful when daily follow limit already hit")
    args = parser.parse_args()

    # Random startup jitter (0–20 min) to avoid mechanical fixed-time patterns.
    # Skipped when running a specific --flow or --no-jitter for manual testing.
    if not args.flow and not args.no_jitter:
        jitter = random.randint(0, 1200)
        logger.info(f"Startup jitter: sleeping {jitter}s before run")
        time.sleep(jitter)

    init_db()
    import_follow_list_if_needed()

    start = time.time()
    results = {}

    logger.info("=" * 60)
    logger.info(f"bei-x bot started — {datetime.now():%Y-%m-%d %H:%M}")
    logger.info("=" * 60)

    flow2_result = {"can_engage_urls": []}

    # Flow 1: Team account reposts
    if not args.flow or args.flow == 1:
        logger.info("Flow 1: Team account reposts...")
        try:
            results["flow1"] = x_bot.repost_team_accounts(CONFIG)
            logger.info(f"Flow 1 done: {results['flow1']}")
        except Exception as e:
            logger.error(f"Flow 1 failed: {e}", exc_info=True)
            results["flow1"] = {"error": str(e)}

    # Flow 2: Deep Research search + repost
    if not args.flow or args.flow == 2:
        logger.info("Flow 2: Deep Research search + repost...")
        try:
            flow2_result = x_bot.search_and_repost_dr(CONFIG)
            results["flow2"] = {k: v for k, v in flow2_result.items() if k != "can_engage_urls"}
            results["flow2"]["can_engage_count"] = len(flow2_result.get("can_engage_urls", []))
            logger.info(f"Flow 2 done: {results['flow2']}")
        except Exception as e:
            logger.error(f"Flow 2 failed: {e}", exc_info=True)
            results["flow2"] = {"error": str(e)}

    # Flow 3: Comment queue (uses can_engage posts from Flow 2)
    if not args.flow or args.flow == 3:
        logger.info("Flow 3: Generating comments for review queue...")
        try:
            results["flow3"] = x_bot.search_and_queue_comments(
                CONFIG,
                can_engage_posts=flow2_result.get("can_engage_urls", [])
            )
            logger.info(f"Flow 3 done: {results['flow3']}")
        except Exception as e:
            logger.error(f"Flow 3 failed: {e}", exc_info=True)
            results["flow3"] = {"error": str(e)}

    # Flow 4: Follow batch
    if (not args.flow or args.flow == 4) and not args.skip_follow:
        logger.info("Flow 4: Following queued accounts...")
        try:
            results["flow4"] = x_bot.follow_daily_batch(CONFIG)
            logger.info(f"Flow 4 done: {results['flow4']}")
        except Exception as e:
            logger.error(f"Flow 4 failed: {e}", exc_info=True)
            results["flow4"] = {"error": str(e)}

    # Flow 5: Reply to high-engagement posts (uses can_engage from Flow 2)
    if not args.flow or args.flow == 5:
        logger.info("Flow 5: Replying to engage-worthy posts...")
        try:
            results["flow5"] = x_bot.reply_to_engage_posts(
                CONFIG,
                can_engage_posts=flow2_result.get("can_engage_urls", [])
            )
            logger.info(f"Flow 5 done: {results['flow5']}")
        except Exception as e:
            logger.error(f"Flow 5 failed: {e}", exc_info=True)
            results["flow5"] = {"error": str(e)}

    # Flow 6: Browse home feed + tiered engagement (reply/repost/quote)
    if not args.flow or args.flow == 6:
        logger.info("Flow 6: Browsing feed for engagement...")
        try:
            results["flow6"] = x_bot.browse_feed_and_engage(CONFIG)
            logger.info(f"Flow 6 done: {results['flow6']}")
        except Exception as e:
            logger.error(f"Flow 6 failed: {e}", exc_info=True)
            results["flow6"] = {"error": str(e)}

    elapsed = int(time.time() - start)
    logger.info(f"All done in {elapsed}s — {results}")

    summary_file = LOG_DIR / f"summary_{datetime.now():%Y-%m-%d}.json"
    import json as _json
    summary_file.write_text(_json.dumps({
        "date": datetime.now().isoformat(),
        "elapsed_secs": elapsed,
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
