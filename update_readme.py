#!/usr/bin/env python3
"""Regenerate README.md from the source awesome list, enriched with GitHub stars.

- Every list item linking to a github.com repo gets its star count (e.g. `⭐44.6k`)
  and items are sorted by stars within each `## ` section (items without stars last).
- Star counts are cached in .stars_cache.json; when the API is unavailable
  (rate limit, network) the cached value is used and marked with `*`.
- The block between CUSTOM_START/CUSTOM_END in the existing README is kept on top.
- Everything else is copied from the source unchanged.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE_URL = "https://raw.githubusercontent.com/andyrewlee/awesome-agent-orchestrators/main/README.md"
OUTPUT_PATH = "README.md"
CACHE_PATH = ".stars_cache.json"
CUSTOM_START = "<!-- CUSTOM_START -->"
CUSTOM_END = "<!-- CUSTOM_END -->"
MAX_RETRIES = 3

HEADERS = {"User-Agent": "awesome-ranked-ai-orchestrators/1.0"}
if os.environ.get("GITHUB_TOKEN"):
    HEADERS["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

# `- [name](https://github.com/owner/repo) rest` -> (indent, name, owner/repo, rest)
REPO_ITEM_RE = re.compile(r"^(\s*)- \[([^\]]+)\]\(https://github\.com/([^/\s)]+/[^/\s)#]+)/?\)(.*)$")


def fetch(url):
    """GET url, retrying network errors and 5xx responses."""
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except OSError as e:  # URLError, HTTPError and timeouts are all OSErrors
            retryable = not isinstance(e, urllib.error.HTTPError) or e.code >= 500
            if not retryable or attempt == MAX_RETRIES - 1:
                raise
            print(f"retrying {url} ({e})", file=sys.stderr)
            time.sleep(2**attempt)


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_stars(n):
    """Round like GitHub: 676 -> '⭐676', 44580 -> '⭐44.6k', 2000000 -> '⭐2M'."""
    for div, suffix in ((1_000_000, "M"), (1000, "k")):
        if n >= div:
            return f"⭐{n / div:.1f}".removesuffix(".0") + suffix
    return f"⭐{n}"


class Stars:
    """Star counts from the GitHub API, falling back to the cache file."""

    def __init__(self):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                self.cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.cache = {}
        self.rate_limited = False
        self.used_cache = False

    def badge(self, repo):
        """Return (stars or None, badge text); the badge ends in '*' if taken from the cache."""
        if not self.rate_limited:
            try:
                stars = json.loads(fetch(f"https://api.github.com/repos/{repo}"))["stargazers_count"]
                self.cache[repo] = {"stars": stars, "fetched_at": now_utc().isoformat().replace("+00:00", "Z")}
                return stars, format_stars(stars)
            except Exception as e:  # noqa: BLE001 - degrade gracefully per repo
                if isinstance(e, urllib.error.HTTPError) and e.code in (403, 429):
                    print("warning: GitHub API rate limit hit, using the cache for the rest", file=sys.stderr)
                    self.rate_limited = True
                else:
                    print(f"warning: could not fetch stars for {repo}: {e}", file=sys.stderr)
        if repo in self.cache:
            self.used_cache = True
            stars = self.cache[repo]["stars"]
            return stars, format_stars(stars) + "*"
        return None, ""

    def save(self):
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2, sort_keys=True)
        os.replace(tmp, CACHE_PATH)


def read_custom_block():
    """Return the CUSTOM_START..CUSTOM_END lines (markers included) of the current README."""
    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            lines = f.read().split("\n")
        start = lines.index(CUSTOM_START)
        return lines[start : lines.index(CUSTOM_END, start) + 1]
    except (FileNotFoundError, ValueError):
        return []


def rank_section(lines, stars):
    """Add star badges to repo items and sort them by stars; other lines stay in place."""
    slots, items = [], []
    for i, line in enumerate(lines):
        if m := REPO_ITEM_RE.match(line):
            indent, name, repo, rest = m.groups()
            count, badge = stars.badge(repo)
            slots.append(i)
            items.append((count, f"{indent}- [{name}](https://github.com/{repo})" + (f" {badge}" if badge else "") + rest))
    items.sort(key=lambda it: (it[0] is None, -(it[0] or 0)))  # stable: unstarred keep source order
    for i, (_, line) in zip(slots, items):
        lines[i] = line
    return lines


def main():
    source = fetch(SOURCE_URL).decode("utf-8")
    stars = Stars()

    out = read_custom_block() + [""]
    section = []
    for line in source.split("\n") + ["## "]:  # sentinel header flushes the last section
        if line.startswith("## ") and section:
            out += rank_section(section, stars)
            section = []
        section.append(line)

    stars.save()
    out += ["", f"Last updated: {now_utc():%Y-%m-%d %H:%M:%S} UTC"]
    if stars.used_cache:
        out += ["", r"\* Star count reused from a previous run (API unavailable), may be outdated."]

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n") + "\n"
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"wrote {OUTPUT_PATH} ({len(result)} bytes, cache used: {stars.used_cache})")


if __name__ == "__main__":
    main()
