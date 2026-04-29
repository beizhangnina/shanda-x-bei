"""
Thin wrapper around the `browse` CLI.
All commands return parsed JSON or raise BrowseError.
"""
import subprocess
import json
import time
import re
from typing import Optional


class BrowseError(Exception):
    pass


def _run(args: str, timeout: int = 30) -> dict:
    # Always connect to the bei-x dedicated Chrome on port 9223 (persistent profile)
    result = subprocess.run(
        f"browse --ws 9223 {args}",
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = result.stdout.strip()
    if not out:
        return {}
    # browse CLI may emit DEBUG lines before JSON — extract the JSON part
    json_start = out.find("{")
    if json_start == -1:
        return {"raw": out}
    try:
        return json.loads(out[json_start:])
    except json.JSONDecodeError:
        return {"raw": out}


def open_url(url: str) -> dict:
    return _run(f'open "{url}"', timeout=20)


def snapshot() -> str:
    """Returns the accessibility tree as a raw string."""
    data = _run("snapshot", timeout=15)
    return data.get("tree", "")


def screenshot(path: str) -> str:
    _run(f'screenshot "{path}"', timeout=15)
    return path


def click(ref: str) -> bool:
    data = _run(f"click @{ref}", timeout=10)
    return data.get("clicked", False)


def click_xy(x: int, y: int) -> bool:
    data = _run(f"click {x} {y}", timeout=10)
    return data.get("clicked", False)


def type_text(text: str) -> bool:
    # Escape single quotes in text
    safe = text.replace("'", "\\'")
    data = _run(f"type '{safe}'", timeout=15)
    return data.get("typed", False)


def press(key: str) -> bool:
    data = _run(f"press '{key}'", timeout=10)
    return bool(data)


def scroll(x: int, y: int, dx: int, dy: int) -> bool:
    data = _run(f"scroll {x} {y} {dx} {dy}", timeout=10)
    return data.get("scrolled", False)


def back() -> bool:
    data = _run("back", timeout=10)
    return bool(data)


def get_url() -> str:
    data = _run("get url", timeout=10)
    return data.get("url", "")


def get_box(ref_or_selector: str) -> Optional[dict]:
    """Get bounding box {x, y} of element by accessibility ref ('0-123') or CSS selector."""
    if re.match(r'^\d+-\d+$', ref_or_selector):
        data = _run(f"get box @{ref_or_selector}", timeout=5)
    else:
        data = _run(f"get box {ref_or_selector}", timeout=5)
    if isinstance(data, dict) and "x" in data:
        return {"x": int(data["x"]), "y": int(data["y"])}
    return None


def find_refs(tree: str, pattern: str) -> list[str]:
    """Find element refs matching pattern in the accessibility tree."""
    return re.findall(rf'\[(\d+-\d+)\] {pattern}', tree)


def find_text_refs(tree: str, text: str) -> list[str]:
    """Find refs of elements containing given text."""
    escaped = re.escape(text)
    return re.findall(rf'\[(\d+-\d+)\][^\n]*{escaped}', tree)


def eval_js(expression: str):
    """Evaluate JavaScript in the page. Returns the result value or None."""
    import shlex
    data = _run(f"eval {shlex.quote(expression)}", timeout=15)
    return data.get("result")


def wait_for(selector: str, timeout_ms: int = 10000) -> bool:
    """Wait for a CSS selector to become visible. Returns True if found."""
    import shlex
    data = _run(f"wait selector {shlex.quote(selector)} -t {timeout_ms}", timeout=timeout_ms // 1000 + 5)
    return bool(data) and "error" not in str(data).lower()


def js_click(selector: str) -> bool:
    """Click an element via JavaScript using a CSS selector. Most reliable click method."""
    result = eval_js(
        f"(() => {{ const el = document.querySelector('{selector}');"
        f" if (!el) return false; el.click(); return true; }})()"
    )
    return result is True


def js_exists(selector: str) -> bool:
    """Check if a CSS selector exists in the DOM."""
    result = eval_js(f"!!document.querySelector('{selector}')")
    return result is True


def wait_seconds(n: float):
    time.sleep(n)
