# qiita_to_elastic

## 0.全体構成

```Mermaid
flowchart LR
  A[Qiita API] --> B[Markdown生成]
  B --> C[tmp/qiita_markdown/*.md]
  C --> D[Markdown解析]
  D --> E[Elasticsearch]
  E --> F[Kibana]
```

できること：

`キーワードが、どの記事の title/body/tags に含まれているか検索できる`

事前に以下リンク先を参考にElasticSearch構築を実施していること。

https://qiita.com/naritomo08/items/8368c2f57803e471cc2f

## 1. ディレクトリ作成

```bash
mkdir -p scripts tmp logs
```

## 2. Elasticsearch Index作成

まずは qiita-articles Index を作成します。

```bash
curl -X PUT "http://elastic1:9200/qiita-articles" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": {
      "properties": {
        "id":          { "type": "keyword" },
        "source":      { "type": "keyword" },
        "user":        { "type": "keyword" },
        "title":       { "type": "text" },
        "url":         { "type": "keyword" },
        "created_at":  { "type": "date" },
        "updated_at":  { "type": "date" },
        "imported_at": { "type": "date" },
        "tags":        { "type": "keyword" },
        "body":        { "type": "text" }
      }
    }
  }'
```

確認

```bash
curl -s "http://elastic1:9200/qiita-articles?pretty"
```

## 3. Qiita全記事取得スクリプト

```bash
cat > scripts/fetch_qiita_articles.py <<'EOF'
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
EOF

chmod +x scripts/fetch_qiita_articles.py
```

## 4. Elasticsearch完全同期スクリプト

```bash
cat > scripts/import_qiita_markdown_to_es.py <<'EOF'
#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

def request_json(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            body = res.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[ERROR] HTTP {e.code}: {body}")

def index_exists(es_url, index):
    req = urllib.request.Request(f"{es_url}/{index}", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else (_ for _ in ()).throw(e)

def create_index(es_url, index):
    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "source": {"type": "keyword"},
                "user": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "title": {"type": "text"},
                "url": {"type": "keyword"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "imported_at": {"type": "date"},
                "tags": {"type": "keyword"},
                "body": {"type": "text"}
            }
        }
    }
    request_json(
        f"{es_url}/{index}",
        method="PUT",
        data=json.dumps(mapping, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

def parse_front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text

    meta_text, body = m.group(1), m.group(2)
    meta = {}

    for line in meta_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key == "title":
            try:
                value = json.loads(value)
            except Exception:
                value = value.strip('"')
        elif key == "tags":
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                value = [
                    x.strip()
                    for x in value[1:-1].split(",")
                    if x.strip()
                ]
            else:
                value = []
        meta[key] = value

    return meta, body

def doc_id_from_meta(meta, path):
    url = meta.get("url", "")
    if "/items/" in url:
        return url.rstrip("/").split("/")[-1]
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()

def read_markdown_docs(markdown_dir, user):
    docs = []
    for path in sorted(Path(markdown_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(text)
        doc_id = doc_id_from_meta(meta, path)

        docs.append({
            "_id": doc_id,
            "id": doc_id,
            "source": "qiita",
            "user": user,
            "filename": path.name,
            "title": meta.get("title", ""),
            "url": meta.get("url", ""),
            "created_at": meta.get("created_at", None),
            "updated_at": meta.get("updated_at", None),
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "tags": meta.get("tags", []),
            "body": body,
        })
    return docs

def bulk_import(es_url, index, docs):
    lines = []
    for doc in docs:
        doc_id = doc.pop("_id")
        lines.append(json.dumps({"index": {"_index": index, "_id": doc_id}}, ensure_ascii=False))
        lines.append(json.dumps(doc, ensure_ascii=False))

    if not lines:
        print("[WARN] no markdown files")
        return

    result = request_json(
        f"{es_url}/_bulk",
        method="POST",
        data=("\n".join(lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
    )

    if result.get("errors"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit("[ERROR] bulk import failed")

    print(f"[INFO] imported/updated {len(docs)} markdown article(s)")

def existing_ids(es_url, index, user):
    query = {
        "size": 10000,
        "_source": False,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"source": "qiita"}},
                    {"term": {"user": user}}
                ]
            }
        }
    }
    result = request_json(
        f"{es_url}/{index}/_search",
        method="POST",
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return {hit["_id"] for hit in result.get("hits", {}).get("hits", [])}

def bulk_delete(es_url, index, ids):
    if not ids:
        print("[INFO] no deleted articles")
        return

    lines = [json.dumps({"delete": {"_index": index, "_id": i}}, ensure_ascii=False) for i in sorted(ids)]
    result = request_json(
        f"{es_url}/_bulk",
        method="POST",
        data=("\n".join(lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
    )

    if result.get("errors"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit("[ERROR] bulk delete failed")

    print(f"[INFO] deleted {len(ids)} old article(s)")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-dir", default="tmp/qiita_markdown")
    parser.add_argument("--es-url", default="http://elastic1:9200")
    parser.add_argument("--index", default="qiita-articles")
    parser.add_argument("--user", default="naritomo08")
    parser.add_argument("--delete-missing", action="store_true")
    args = parser.parse_args()

    es_url = args.es_url.rstrip("/")

    if not index_exists(es_url, args.index):
        create_index(es_url, args.index)

    docs = read_markdown_docs(args.markdown_dir, args.user)
    current_ids = {d["_id"] for d in docs}

    if args.delete_missing:
        bulk_delete(es_url, args.index, existing_ids(es_url, args.index, args.user) - current_ids)

    bulk_import(es_url, args.index, docs)
    request_json(f"{es_url}/{args.index}/_refresh", method="POST")
    print("[INFO] markdown sync completed")

if __name__ == "__main__":
    main()
EOF

chmod +x scripts/import_qiita_markdown_to_es.py
```

## 5. 手動実行

全記事取得

```bash
python3 scripts/fetch_qiita_articles.py \
  --user <Qiitaユーザー名> \
  --markdown-dir tmp/qiita_markdown
```

Elasticsearchへ完全同期

```bash
python3 scripts/import_qiita_markdown_to_es.py \
  --markdown-dir tmp/qiita_markdown \
  --es-url http://elastic1:9200 \
  --index <Qiitaユーザー名> \
  --user naritomo08 \
  --delete-missing
```

--delete-missing により以下になります。

|Qiita側|Elasticsearch側|
|---|---|
|新規記事|追加|
|更新記事|上書き|
|既存記事|維持|
|削除済み記事|削除|

## 6. 自動同期シェル

```bash
cat > scripts/sync_qiita_all_to_es.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${BASE_DIR}"

ES_URL="${ES_URL:-http://elastic1:9200}"
INDEX="${INDEX:-qiita-articles}"
QIITA_USER="${QIITA_USER:-naritomo08}"

mkdir -p tmp logs

LOG_FILE="logs/sync_qiita_all_to_es.log"

{
  echo "[$(date '+%F %T')] sync start"

  rm -rf tmp/qiita_markdown
  mkdir -p tmp/qiita_markdown

  python3 scripts/fetch_qiita_articles.py \
    --user "${QIITA_USER}" \
    --markdown-dir tmp/qiita_markdown

  python3 scripts/import_qiita_markdown_to_es.py \
    --markdown-dir tmp/qiita_markdown \
    --es-url "${ES_URL}" \
    --index "${INDEX}" \
    --user "${QIITA_USER}" \
    --delete-missing

  echo "[$(date '+%F %T')] sync completed"
} >> "${LOG_FILE}" 2>&1
EOF

chmod +x scripts/sync_qiita_all_to_es.sh
```

実行：

```bash
export QIITA_USER=<Qiitaユーザー名>
./scripts/sync_qiita_all_to_es.sh
```

ログ確認：

```bash
tail -f logs/sync_qiita_all_to_es.log
```

## 7. cronで自動完全同期

```bash
crontab -e
```

設定例

```bash
5 3 * * * export QIITA_USER=<Qiitaユーザー名> && cd ~/qiita_to_elastic && ./scripts/sync_qiita_all_to_es.sh
```

## 8. Elasticsearch確認

```bash
curl -s "http://elastic1:9200/qiita-articles/_count?pretty"
```

キーワード検索：

```bash
curl -s "http://elastic1:9200/qiita-articles/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "multi_match": {
        "query": "Iceberg Elasticsearch",
        "fields": ["title^3", "body", "tags"]
      }
    },
    "_source": ["title", "url", "tags", "updated_at"]
  }'
```

タイトルとURLだけ見たい場合：

```bash
curl -s "http://elastic1:9200/qiita-articles/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "_source": ["title", "url", "tags", "updated_at"],
    "query": {
      "multi_match": {
        "query": "Hive Metastore PostgreSQL",
        "fields": ["title^3", "body", "tags"]
      }
    }
  }' | jq '.hits.hits[]._source'
```

## 9. Kibana設定

Kibanaで Data View を作成します。

Name: qiita-articles
Index pattern: qiita-articles
Time field: updated_at

Discoverで検索例：

body : "Iceberg"
title : "Hive"
tags : "hadoop"
body : "PostgreSQL" and body : "Metastore"

## 10. 使い方イメージ

例えば Kibana で、

body : "Keepalived"

と検索すると、Keepalived について書いた記事を一覧できます。

body : "Iceberg" and body : "Flink"

なら、Iceberg と Flink の両方に触れている記事を探せます。

これで、Qiita全記事を Elasticsearch上の技術ナレッジDB として完全同期できます。
