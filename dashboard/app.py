#!/usr/bin/env python3
"""
Social Bot Dashboard — Flask web app.
Run: python dashboard/app.py
Open: http://localhost:5050
"""
import sys
import json
from pathlib import Path
from datetime import date, timedelta
from flask import Flask, render_template, jsonify
from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).parent.parent))
from bot.db import get_stats, get_recent_replies, get_today_count, init_db, get_pending_reviews, update_review_status, log_reply

app = Flask(__name__)
CORS(app)

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/overview")
def api_overview():
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    stats = get_stats(days=30)
    today_stats = [s for s in stats if s["day"] == today]
    ytd_stats   = [s for s in stats if s["day"] == yesterday]

    def sum_field(rows, field):
        return sum(r.get(field, 0) for r in rows)

    x_today = next((s for s in today_stats if s["platform"] == "x"), {})
    x_ytd   = next((s for s in ytd_stats  if s["platform"] == "x"), {})

    from bot.db import get_follow_stats
    follow_stats = get_follow_stats()

    return jsonify({
        "today": {
            "x_posted":     x_today.get("posted", 0),
            "x_target":     CONFIG["x"]["max_daily_reposts"],
            "total_posted": x_today.get("posted", 0),
        },
        "yesterday": {
            "x_posted": x_ytd.get("posted", 0),
        },
        "total_all_time": sum_field(stats, "posted"),
        "follow_stats": follow_stats,
        "miromind_mentions": _count_miromind_mentions(),
    })


@app.route("/api/chart/daily")
def api_chart_daily():
    stats = get_stats(days=30)
    # Build per-day totals
    days = {}
    for s in stats:
        d = s["day"]
        if d not in days:
            days[d] = {"x": 0, "reddit": 0}
        days[d][s["platform"]] = s.get("posted", 0)

    labels = sorted(days.keys())
    return jsonify({
        "labels": labels,
        "x":      [days[d]["x"]      for d in labels],
        "reddit": [days[d]["reddit"] for d in labels],
    })


@app.route("/api/replies")
def api_replies():
    replies = get_recent_replies(limit=100)
    return jsonify(replies)


@app.route("/api/review/pending")
def api_review_pending():
    items = get_pending_reviews(limit=20)
    return jsonify(items)


@app.route("/api/review/<int:review_id>/approve", methods=["POST"])
def api_review_approve(review_id):
    items = get_pending_reviews(limit=100)
    item = next((i for i in items if i["id"] == review_id), None)
    if not item:
        return jsonify({"error": "not found"}), 404
    update_review_status(review_id, "approved")
    log_reply(
        "x",
        item["post_url"],
        "DR Community",
        (item.get("post_content") or "")[:100],
        item["suggested_comment"],
        None,
        "posted"
    )
    return jsonify({"status": "approved", "id": review_id})


@app.route("/api/review/<int:review_id>/reject", methods=["POST"])
def api_review_reject(review_id):
    update_review_status(review_id, "rejected")
    return jsonify({"status": "rejected", "id": review_id})


def _count_miromind_mentions() -> int:
    from bot.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM replies WHERE reply_text LIKE '%MiroMind%' AND status='posted'"
        ).fetchone()
        return row["cnt"] if row else 0


if __name__ == "__main__":
    init_db()
    print("Dashboard: http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
