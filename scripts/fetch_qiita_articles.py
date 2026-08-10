#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://qiita.com/api/v2"

def request_json(url: str, token: str | None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "qiita-articles-es-sync/0.1")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))

def safe_name(item: dict) -> str:
    created = item.get("created_at", "")[:10]
    title = item.get("title", "untitled")
    keep = []

    for ch in title:
        if ch.isalnum() or ch in "-_一-龥ぁ-んァ-ンー":
            keep.append(ch)
        else:
            keep.append("_")

    name = "".join(keep).strip("_")[:80] or "article"
    return f"{created}_{name}.md"

def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Qiita articles by user.")
    parser.add_argument("--user", default="naritomo08")
    parser.add_argument("--tag", default=None)
    parser.add_argument("--markdown-dir", default="tmp/qiita_markdown")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()

    token = os.environ.get("QIITA_TOKEN")

    all_items = []

    for page in range(1, args.max_pages + 1):
        if args.tag:
            query = urllib.parse.quote(f"user:{args.user} tag:{args.tag}")
            url = f"{API_BASE}/items?page={page}&per_page={args.per_page}&query={query}"
        else:
            user = urllib.parse.quote(args.user, safe="")
            url = f"{API_BASE}/users/{user}/items?page={page}&per_page={args.per_page}"
        items = request_json(url, token)

        if not items:
            break

        all_items.extend(items)

        if len(items) < args.per_page:
            break

        time.sleep(args.sleep)

    md_dir = Path(args.markdown_dir)
    md_dir.mkdir(parents=True, exist_ok=True)

    for item in all_items:
        tags = [t.get("name", "") for t in item.get("tags", [])]

        header = "\n".join([
            "---",
            f"title: {json.dumps(item.get('title', ''), ensure_ascii=False)}",
            f"url: {item.get('url', '')}",
            f"created_at: {item.get('created_at', '')}",
            f"updated_at: {item.get('updated_at', '')}",
            "tags: [" + ", ".join(tags) + "]",
            "---",
            "",
        ])

        body = item.get("body") or ""
        (md_dir / safe_name(item)).write_text(header + body, encoding="utf-8")

    print(f"fetched {len(all_items)} item(s)")
    print(f"wrote markdown files to {md_dir}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)