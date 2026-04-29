# bei-x: AI-Powered X/Twitter Engagement Bot

Automated social presence for [@bei_zhang01](https://x.com/bei_zhang01) — browse feed, repost, reply, quote, and follow with AI-driven decision making.

Built with Claude + `browse` CLI for browser automation. No Twitter API keys needed.

## What It Does

The bot runs 6 flows daily, combining **brand presence** (30%) with **genuine AI community engagement** (70%):

### Flow 1 — Team Reposts
Scans 12 team member timelines, reposts relevant content within 7 days.
- Claude Haiku filters (default: repost unless clearly off-topic)
- Random 4-7 min intervals between reposts

### Flow 2 — Brand Search + Repost
Searches MiroMind/MiroThinker/Deep Research keywords, reposts high-quality mentions.
- Engagement floor: 50+ (likes + reposts + replies)
- AI spam detection (filters templated bot promos)
- Auto-enqueues KOL promoters to follow queue

### Flow 3 — Comment Queue
Generates AI comment drafts for engage-worthy posts. Stored in review queue for manual approval — **not auto-posted**.

### Flow 4 — Follow Batch
Follows accounts from a curated queue (2000+ handles) + KOLs discovered by Flow 2.
- 30-60s random intervals
- 20/day cap

### Flow 5 — Reply to DR Posts
Auto-replies to high-engagement Deep Research posts with Claude Sonnet-generated comments.
- 10-15 min intervals
- 5/day cap

### Flow 6 — Home Feed Engagement (main flow)
Browses the home feed, scrolls 8 screens, and engages with a tiered strategy:

| Tier | Threshold | Action | Daily Cap | Interval |
|------|-----------|--------|-----------|----------|
| Reply | 50+ engagement | 1-2 sentence acknowledgment | 10 | 6-9 min |
| Repost | 200+ engagement | Share to followers | 5 | 10-13 min |
| Quote | 500+ engagement + big account | 2-3 sentence technical insight | 3 | 17-20 min |

AI decides the tier per post. Graceful fallback when a tier cap is reached (Quote -> Repost -> Reply).

Quote tweets are **pure technical insight** — never mention MiroMind or any product.

## Architecture

```
run_daily.py          Orchestrator — runs all 6 flows sequentially
  bot/x_bot.py        Flow logic — navigation, actions, rate limiting
  bot/browser.py      browse CLI wrapper — js_click, eval_js, open_url
  bot/ai_engine.py    Claude via OpenRouter — scoring, decisions, generation
  bot/db.py           SQLite — dedup, tracking, review queue, follow queue
config.json           All thresholds, delays, accounts, queries
```

**Browser automation**: Uses `browse` CLI connected to a dedicated Chrome instance (port 9223) with persistent login. All clicks use `js_click()` (JavaScript `element.click()`) — the most reliable method.

**AI models**: Claude Haiku for cheap/fast filtering and decisions. Claude Sonnet for quality comment generation.

## Setup

### Prerequisites
- Python 3.9+
- [browse CLI](https://www.npmjs.com/package/@anthropic-ai/browse-cli) (`npm install -g @anthropic-ai/browse-cli`)
- Chrome running with remote debugging: `open -a "Google Chrome" --args --remote-debugging-port=9223`
- OpenRouter API key

### Install

```bash
git clone https://github.com/beizhangnina/shanda-x-bei.git
cd shanda-x-bei
pip install openai python-dotenv flask

# Create .env
echo "OPENROUTER_API_KEY=sk-or-v1-your-key-here" > .env

# Log in to X manually in the Chrome window, then:
python run_daily.py --no-jitter
```

### Usage

```bash
python run_daily.py              # run all 6 flows (with random startup jitter)
python run_daily.py --no-jitter  # skip jitter (for manual runs)
python run_daily.py --flow 6     # run only feed engagement
python run_daily.py --flow 1     # run only team reposts
python run_daily.py --skip-follow  # skip Flow 4
```

### Scheduled Run (macOS)

A LaunchAgent plist is included for daily automated execution:
```bash
cp launchd/com.socialbot.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.socialbot.daily.plist
```

## Config

Edit `config.json` to customize:
- **team_accounts**: List of team member handles to repost
- **search_queries**: Keywords for brand search
- **feed_engagement**: Engagement thresholds and daily caps for each tier
- **persona**: Name, role, and style for AI-generated content
- **min_repost_delay / max_repost_delay**: Random interval range (seconds)

## Safety

- **Rate limiting**: Random intervals between all actions, daily caps per action type
- **Startup jitter**: 0-20 min random delay to avoid fixed-time patterns
- **Dedup**: SQLite tracks every action — never engages the same post twice
- **Engagement floor**: Skips low-traction posts (no API cost wasted)
- **Spam detection**: AI filters out templated bot promotions
- **Review queue**: Comments can be queued for manual approval before posting

## Project Structure

```
.
├── run_daily.py           # Entry point
├── config.json            # All configuration
├── .env                   # API keys (gitignored)
├── follow_list.txt        # Bulk follow handles
├── bot/
│   ├── x_bot.py           # 6 flow functions + helpers
│   ├── browser.py         # browse CLI wrapper
│   ├── ai_engine.py       # Claude AI functions
│   ├── db.py              # SQLite schema + queries
│   └── reddit_bot.py      # (unused, legacy)
├── dashboard/
│   └── app.py             # Flask dashboard (optional)
├── launchd/
│   └── com.socialbot.daily.plist
└── logs/                   # Daily logs + SQLite DB
```
