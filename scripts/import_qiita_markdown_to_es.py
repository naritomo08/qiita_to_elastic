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
