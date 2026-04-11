# bei-x Social Bot — Design Spec
**Date:** 2026-04-10  
**Author:** Bei Zhang (@bei_zhang01)  
**Status:** Approved

---

## Context

Bei Zhang is the Growth VP at Miromind (miromind.ai) — an enterprise AI company building MiroThinker (235B-param model) + MiroMind OS, focused on verifiable accuracy and System 2 reasoning.

**Goal:** Run @bei_zhang01 as a high-presence account in the Deep Research AI community — amplifying Miromind's team content, curating the best DR content, engaging with the community, and growing a targeted follower network.

**Reference codebase:** [github.com/mguozhen/social-bot](https://github.com/mguozhen/social-bot)  
**Approach:** Fork social-bot, minimal changes, extend existing patterns.

---

## Architecture

```
bei-x/
├── bot/
│   ├── browser.py          ← unchanged
│   ├── db.py               ← add review_queue + follow_queue tables
│   ├── ai_engine.py        ← update persona + 3 new prompts
│   ├── x_bot.py            ← main changes: 4 new functions
│   └── __init__.py         ← unchanged
├── dashboard/
│   ├── app.py              ← add /api/review endpoints
│   └── templates/          ← add Review tab
├── launchd/                ← unchanged
├── run_daily.py            ← update execution order
├── config.json             ← update with Miromind accounts + keywords
├── follow_list.txt         ← ~2000 handles to follow, one per line
├── .env                    ← unchanged (ANTHROPIC_API_KEY)
└── requirements.txt        ← unchanged
```

---

## Four Daily Flows

### Flow 1 — Team Repost (fully automatic)
- Monitor 12 team accounts: `@miromind_ai @tianqiao_chen @LidongBing @KaiyuYang4 @SimonShaoleiDu @BinWang_Eng @Yifan_Zhang77 @JeremyFeng98 @XingxuanLi @Yang_zy223 @christlurker @howtocluster`
- Visit each account's timeline, extract posts since last run
- Claude evaluates: REPOST or SKIP (skip personal/unrelated content)
- Auto-repost relevant posts, log to `replies` table

### Flow 2 — Deep Research Curation (fully automatic)
- Search X for keywords: `"Deep Research AI"`, `"#DeepResearch"`, `"deep reasoning AI"`, `"AI reasoning verification"`, `"system 2 AI"`, `"verifiable AI"`
- Claude scores quality: `{"quality": "high/low", "can_engage": true/false}`
- Auto-repost high-quality hits
- Flag `can_engage=true` posts for Flow 3

### Flow 3 — Community Engagement (semi-automatic, review required)
- For `can_engage=true` posts from Flow 2
- Claude generates a comment in Bei Zhang's voice
- Insert into `review_queue` with `status='pending'`
- User reviews in Flask dashboard → Approve triggers `post_reply()`, Reject marks rejected
- **Nothing is posted without user approval**

### Flow 4 — Smart Follow (fully automatic, 15/day cap)
- On first run: import `follow_list.txt` into `follow_queue` table
- Daily: SELECT 15 pending handles → follow via browser → mark `followed`
- During Flow 2/3: if encountered account has high influence → `maybe_enqueue_influencer()`
- Skip already-followed accounts (checked by `handle UNIQUE` constraint)

---

## Config

**config.json:**
```json
{
  "platforms": {
    "x": {
      "account": "@bei_zhang01",
      "max_daily_reposts": 15,
      "max_daily_comments": 10,
      "max_daily_follows": 15,
      "min_interval_seconds": 300,
      "influencer_follower_threshold": 5000,
      "team_accounts": [
        "@miromind_ai", "@tianqiao_chen", "@LidongBing",
        "@KaiyuYang4", "@SimonShaoleiDu", "@BinWang_Eng",
        "@Yifan_Zhang77", "@JeremyFeng98", "@XingxuanLi",
        "@Yang_zy223", "@christlurker", "@howtocluster"
      ],
      "search_queries": [
        "Deep Research AI", "#DeepResearch",
        "deep reasoning AI", "AI reasoning verification",
        "system 2 AI", "verifiable AI"
      ]
    }
  },
  "product": {
    "name": "MiroMind",
    "url": "miromind.ai",
    "keywords": ["deep research", "reasoning", "verifiable AI", "system 2"],
    "description": "Enterprise AI with verifiable accuracy — MiroThinker 235B + MiroMind OS"
  },
  "persona": {
    "name": "Bei Zhang",
    "role": "Growth VP at MiroMind",
    "style": "Insightful, technically credible, not salesy. Lead with genuine value, mention MiroMind only when it fits naturally."
  }
}
```

---

## Database Changes

**Unchanged tables:** `replies`, `daily_stats`, `leads`

**New table — review_queue:**
```sql
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_url TEXT NOT NULL,
    post_content TEXT,
    suggested_comment TEXT NOT NULL,
    status TEXT DEFAULT 'pending',   -- pending / approved / rejected
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    actioned_at TIMESTAMP
);
```

**New table — follow_queue:**
```sql
CREATE TABLE IF NOT EXISTS follow_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT UNIQUE NOT NULL,
    source TEXT DEFAULT 'manual',    -- manual / dr_discovered
    status TEXT DEFAULT 'pending',   -- pending / followed / skipped
    followed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_follow_status ON follow_queue(status);
```

---

## AI Prompts

**Prompt 1 — Team content filter (Flow 1):**
```
You are Bei Zhang, Growth VP at MiroMind.
Decide if this tweet is worth retweeting from @bei_zhang01:
- Related to AI research, deep reasoning, enterprise AI → REPOST
- Team member's technical insight, paper, product update → REPOST
- Personal life, unrelated topic, casual retweet → SKIP
Output only REPOST or SKIP.
```

**Prompt 2 — DR content quality scoring (Flow 2/3, first layer):**
```
Is the following tweet high-quality Deep Research content
(academic progress, technical discussion, industry insight)?
Output JSON: {"quality": "high/low", "can_engage": true/false}
can_engage=true means there is natural space to add a meaningful comment.
```

**Prompt 3 — Comment generation (Flow 3):**
```
You are Bei Zhang, Growth VP at MiroMind, building presence in the Deep Research AI community.
Style: insightful, technically credible, never salesy.
Only mention MiroMind when it is genuinely relevant.

Original post: {post_content}

Write a comment (≤280 chars) that:
- Leads with a genuinely valuable insight or addition
- Naturally mentions MiroMind's verification approach ONLY if highly relevant
- Otherwise builds personal influence without product mention
Output plain text, no quotes.
```

---

## Dashboard — Review Tab

New tab in Flask dashboard showing pending comments:

```
┌─────────────────────────────────────────────────┐
│  bei-x Dashboard          [Today's Stats] [Review] │
├─────────────────────────────────────────────────┤
│  📋 Pending Review  (N)                          │
│                                                  │
│  ┌── Original Post ───────────────────────────┐ │
│  │ @handle · Xh ago                           │ │
│  │ "post content..."                          │ │
│  ├── Suggested Comment ─────────────────────── │ │
│  │ "AI-generated comment..."                  │ │
│  │                                             │ │
│  │          [✅ Approve]  [❌ Reject]           │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

**New API endpoints (app.py):**
- `POST /api/review/<id>/approve` → calls `post_reply()`, logs to `replies`, marks `approved`
- `POST /api/review/<id>/reject` → marks `rejected`

---

## New Functions in x_bot.py

| Function | Flow | Lines (est.) | Notes |
|---|---|---|---|
| `repost_team_accounts(config)` | 1 | ~50 | Visit timeline → Claude → repost |
| `search_and_repost_dr(config)` | 2 | ~40 | Keyword search → Claude → repost |
| `search_and_queue_comments(config)` | 3 | ~40 | can_engage posts → Claude → review_queue |
| `follow_daily_batch(config)` | 4 | ~30 | 15 pending handles → follow |
| `maybe_enqueue_influencer(handle, count)` | 4 | ~10 | If followers > threshold → queue |

**Reused unchanged from social-bot:**
- `browse_x_search()` — search logic
- `post_reply()` — posting comments
- `already_replied()` — deduplication

---

## run_daily.py Execution Order

```python
import_follow_list_if_needed()     # one-time: load follow_list.txt → follow_queue
repost_team_accounts(config)       # Flow 1 (~5 min)
search_and_repost_dr(config)       # Flow 2 (~5 min)
search_and_queue_comments(config)  # Flow 3 (~5 min, no posts sent)
follow_daily_batch(config)         # Flow 4 (~10 min, 15 follows)
```

Total runtime: ~25 min/day. Scheduled via macOS LaunchAgent at 10:05 AM.

---

## Verification

1. **Unit test flows individually** via CLI flags: `python3 run_daily.py --flow 1`
2. **Dashboard review** at `localhost:5050/review` — confirm comments appear and approve/reject works
3. **Follow tracking** — `SELECT COUNT(*) FROM follow_queue WHERE status='followed'` increments by ≤15/day
4. **DR discovery follow** — manually trigger with a known high-follower handle, confirm it appears in queue
5. **Deduplication** — run Flow 1 twice, confirm no duplicate reposts
