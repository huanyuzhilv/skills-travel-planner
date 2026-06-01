"""TikHub 小红书：搜图与正文素材（供 fill_xhs / enrich / generate 使用）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from repo_dotenv import load_repo_dotenv
from tikhub_xhs_cache import XhsNoteCache, get_active_cache
from tikhub_xhs_client import (
    TikHubXhsClient,
    extract_image_urls_from_detail_payload,
    parse_search_results,
    require_api_key,
)


def ensure_tikhub_api_key(root: Path) -> None:
    load_repo_dotenv(root)
    require_api_key()


def preflight_tikhub_search(
    root: Path,
    timeout_ms: int,
    *,
    cooldown_ms: int = 0,
    throttle_sleep_ms: Callable[[int], None] | None = None,
) -> None:
    """TikHub 预检：搜索接口可用且能返回笔记。"""
    sleep = throttle_sleep_ms or (lambda _ms: None)
    load_repo_dotenv(root)
    client = TikHubXhsClient(timeout=max(30.0, float(timeout_ms) / 1000.0))
    raw = client.search_notes("旅行 风景", page=1)
    sleep(cooldown_ms)
    if not parse_search_results(raw):
        raise RuntimeError(
            "TikHub 预检失败：search_notes 未返回可用笔记。"
            "请检查 TIKHUB_API_KEY、账户余额与 https://docs.tikhub.io"
        )


def _score_feed(item: dict[str, Any]) -> int:
    try:
        return int(item.get("liked_count") or 0) + int(item.get("collected_count") or 0)
    except (TypeError, ValueError):
        return 0


def fetch_slot_images_via_tikhub(
    root: Path,
    keyword: str,
    min_images: int,
    max_images: int,
    timeout_ms: int,
    *,
    retries: int = 3,
    cooldown_ms: int = 0,
    max_feed_details: int = 6,
    exclude_fingerprints: set[str] | None = None,
    max_images_per_feed: int = 5,
    normalize_url,
    fingerprint_image_url,
    throttle_sleep_ms,
) -> tuple[list[str], list[dict[str, Any]]]:
    """按关键词搜笔记并取图（TikHub REST）。"""
    load_repo_dotenv(root)
    exclude = exclude_fingerprints or set()
    detail_cap = max(1, min(max_feed_details, 24))
    client_timeout = max(30.0, float(timeout_ms) / 1000.0)
    cache = get_active_cache()

    for attempt in range(max(1, retries)):
        urls: list[str] = []
        sources: list[dict[str, Any]] = []
        seen_raw: set[str] = set()
        local_fp: set[str] = set()
        try:
            client = TikHubXhsClient(timeout=client_timeout)
            search_cached = cache.get_search(keyword) if cache else None
            if search_cached is not None:
                feeds = list(search_cached)
            else:
                raw = client.search_notes(keyword)
                throttle_sleep_ms(cooldown_ms)
                feeds = parse_search_results(raw)
                if cache and feeds:
                    cache.put_search(keyword, feeds)
            feeds.sort(key=_score_feed, reverse=True)
            scan_cap = min(len(feeds), max(detail_cap, 14 if exclude else detail_cap))

            for item in feeds[:scan_cap]:
                note_id = str(item.get("note_id") or "").strip()
                if not note_id:
                    continue

                def absorb(url_list: list[str]) -> int:
                    feed_added = 0
                    for u in url_list:
                        nu = normalize_url(u)
                        if not nu or nu in seen_raw:
                            continue
                        fp = fingerprint_image_url(nu)
                        if fp:
                            if fp in exclude or fp in local_fp:
                                continue
                            local_fp.add(fp)
                        seen_raw.add(nu)
                        urls.append(nu)
                        feed_added += 1
                        if feed_added >= max(1, max_images_per_feed):
                            break
                        if len(urls) >= max_images:
                            break
                    return feed_added

                feed_added = absorb(list(item.get("image_urls") or []))

                if feed_added < max(1, max_images_per_feed) and len(urls) < max_images:
                    cached_payload = cache.get_detail_payload(note_id) if cache else None
                    if cached_payload is not None:
                        detail_raw = cached_payload
                    else:
                        detail_raw = client.get_note_info(note_id)
                        throttle_sleep_ms(cooldown_ms)
                        if cache and isinstance(detail_raw, dict):
                            cache.put_detail(note_id, detail_raw)
                    detail_urls = extract_image_urls_from_detail_payload(detail_raw)
                    feed_added += absorb(detail_urls)

                if feed_added:
                    sources.append(
                        {
                            "feed_id": note_id,
                            "backend": "tikhub",
                            "image_count": feed_added,
                        }
                    )
                if len(urls) >= max_images:
                    break
                if len(urls) >= min_images:
                    break

            return urls[:max_images], sources
        except Exception:
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
            continue
    return [], []


def search_note_images_by_keyword(
    root: Path,
    keyword: str,
    *,
    limit: int = 10,
    timeout_ms: int = 180_000,
    normalize_url,
    fingerprint_image_url,
    throttle_sleep_ms: Callable[[int], None],
) -> list[str]:
    """供 ``assets/generate.py --auto-images`` 使用。"""
    urls, _ = fetch_slot_images_via_tikhub(
        root,
        keyword,
        1,
        max(1, limit),
        timeout_ms,
        retries=2,
        cooldown_ms=0,
        max_feed_details=6,
        max_images_per_feed=max(1, min(3, limit)),
        normalize_url=normalize_url,
        fingerprint_image_url=fingerprint_image_url,
        throttle_sleep_ms=throttle_sleep_ms,
    )
    return urls[:limit]


def fetch_raw_note_snippets(
    root: Path,
    keywords: list[str],
    timeout_ms: int,
    *,
    max_notes: int,
    retries: int,
    extract_text: Callable[[Any], str],
    throttle_sleep_ms: Callable[[int], None],
) -> str:
    """按关键词拉笔记正文片段（供 ``enrich_daily_descriptions_from_xhs``）。"""
    load_repo_dotenv(root)
    client_timeout = max(30.0, float(timeout_ms) / 1000.0)
    cache = get_active_cache()
    blobs: list[str] = []

    for kw in keywords:
        if len(blobs) >= max_notes:
            break
        last_err: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                client = TikHubXhsClient(timeout=client_timeout)
                search_cached = cache.get_search(kw) if cache else None
                if search_cached is not None:
                    feeds = list(search_cached)
                else:
                    raw = client.search_notes(kw)
                    throttle_sleep_ms(0)
                    feeds = parse_search_results(raw)
                    if cache and feeds:
                        cache.put_search(kw, feeds)
                feeds.sort(key=_score_feed, reverse=True)
                for item in feeds[:5]:
                    if len(blobs) >= max_notes:
                        break
                    note_id = str(item.get("note_id") or "").strip()
                    if not note_id:
                        continue
                    preview = str(item.get("content") or "").strip()
                    blob = preview if len(preview) > 60 else ""
                    if len(blob) < 60:
                        cached_payload = cache.get_detail_payload(note_id) if cache else None
                        if cached_payload is not None:
                            detail_raw = cached_payload
                        else:
                            detail_raw = client.get_note_info(note_id)
                            throttle_sleep_ms(0)
                            if cache and isinstance(detail_raw, dict):
                                cache.put_detail(note_id, detail_raw)
                        blob = extract_text(detail_raw)
                    if len(blob) > 60:
                        blobs.append(blob)
                break
            except Exception as exc:
                last_err = exc
                if attempt + 1 < retries:
                    time.sleep(1.5 * (attempt + 1))
                continue
        if last_err and not blobs:
            print(f"WARN 检索失败 keyword={kw!r}: {last_err}", file=__import__("sys").stderr)

    return "\n".join(blobs)
