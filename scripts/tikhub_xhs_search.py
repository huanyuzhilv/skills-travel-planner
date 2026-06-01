#!/usr/bin/env python3
"""
TikHub 小红书关键词搜索：输出笔记标题、正文与图片 URL。

依赖：仓库根 .env 中的 TIKHUB_API_KEY（或环境变量）。

示例：
  python3 scripts/tikhub_xhs_search.py --keyword "肇兴侗寨 攻略"
  python3 scripts/tikhub_xhs_search.py -k "肇兴侗寨 攻略" --limit 5 --fetch-detail -o out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from repo_dotenv import load_repo_dotenv  # noqa: E402
from tikhub_xhs_client import (  # noqa: E402
    TikHubXhsClient,
    normalize_detail_payload,
    parse_search_results,
)


def _merge_detail(base: dict, detail: dict) -> dict:
    merged = dict(base)
    for key in ("title", "content", "author", "author_id", "type"):
        if detail.get(key):
            merged[key] = detail[key]
    for key in ("liked_count", "collected_count", "comments_count"):
        if detail.get(key) is not None:
            merged[key] = detail[key]
    seen = set(base.get("image_urls") or [])
    urls = list(base.get("image_urls") or [])
    for u in detail.get("image_urls") or []:
        if u not in seen:
            seen.add(u)
            urls.append(u)
    merged["image_urls"] = urls
    merged["detail_fetched"] = True
    return merged


def run_search(
    client: TikHubXhsClient,
    *,
    keyword: str,
    page: int,
    limit: int,
    fetch_detail: bool,
    sort_type: str | None,
    note_type: str | None,
) -> dict:
    raw = client.search_notes(keyword, page=page, sort_type=sort_type, note_type=note_type)
    articles = parse_search_results(raw)
    if limit > 0:
        articles = articles[:limit]

    if fetch_detail:
        enriched = []
        for art in articles:
            note_id = art.get("note_id") or ""
            if not note_id:
                enriched.append(art)
                continue
            try:
                detail_raw = client.get_note_info(note_id)
                detail = normalize_detail_payload(detail_raw)
                enriched.append(_merge_detail(art, detail))
            except Exception as exc:  # noqa: BLE001
                art = dict(art)
                art["detail_error"] = str(exc)
                enriched.append(art)
        articles = enriched

    return {
        "keyword": keyword,
        "page": page,
        "count": len(articles),
        "articles": articles,
    }


def main() -> int:
    load_repo_dotenv(_REPO_ROOT)

    ap = argparse.ArgumentParser(description="TikHub 小红书关键词搜索（标题/正文/图片链接）")
    ap.add_argument("-k", "--keyword", required=True, help="搜索关键词")
    ap.add_argument("--page", type=int, default=1, help="页码，从 1 开始")
    ap.add_argument("--limit", type=int, default=20, help="最多返回条数（默认 20）")
    ap.add_argument(
        "--fetch-detail",
        action="store_true",
        help="对每条结果再请求笔记详情（更全正文与图片，按条计费）",
    )
    ap.add_argument("--sort-type", default=None, help="排序，如 popularity_descending")
    ap.add_argument("--note-type", default=None, help="笔记类型筛选")
    ap.add_argument("-o", "--output", type=Path, default=None, help="写入 JSON 文件；默认打印到 stdout")
    ap.add_argument("--pretty", action="store_true", help="JSON 缩进输出")
    args = ap.parse_args()

    try:
        client = TikHubXhsClient()
        result = run_search(
            client,
            keyword=args.keyword,
            page=args.page,
            limit=args.limit,
            fetch_detail=args.fetch_detail,
            sort_type=args.sort_type,
            note_type=args.note_type,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    indent = 2 if args.pretty else None
    text = json.dumps(result, ensure_ascii=False, indent=indent)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.output}（{result['count']} 条）", file=sys.stderr)
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
