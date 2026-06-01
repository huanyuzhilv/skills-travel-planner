"""TikHub 小红书笔记缓存：fill_xhs 与 enrich_daily 之间共享 search/detail 结果。

设计：
- 一次交付 deliver 内：先跑 enrich_daily（少量关键词 → 详情），再跑 fill_xhs_images（按槽更多关键词）；
  缓存把两个阶段对同一关键词/note_id 的 TikHub 请求合并，命中即跳过付费调用。
- 持久化：``<路书目录>/sources/xhs-note-cache.json``；下次 deliver 也会先尝试命中（默认 TTL 7 天）。
- 进程内单例：``get_active_cache() / set_active_cache(cache)``，被 ``tikhub_xhs_feeds`` 自动调用。
- **线程安全**：``fill_xhs_images.py`` 在多线程并发处理配图槽时会同时读写本缓存，
  所有可变状态（``_searches`` / ``_details`` / ``_dirty`` / ``hits`` / ``misses``）均由 ``_lock`` 保护。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any


_KW_WS_RE = re.compile(r"\s+")
_DEFAULT_TTL_SEC = int(os.environ.get("ROADBOOK_XHS_CACHE_TTL_SEC", str(7 * 24 * 3600)))


def normalize_keyword(keyword: str) -> str:
    """搜索关键词归一化：合并空白，原样保留中文。"""
    return _KW_WS_RE.sub(" ", str(keyword or "").strip())


class XhsNoteCache:
    """两阶段共享的小红书笔记缓存（JSON 文件 + 内存）。"""

    def __init__(self, path: Path, *, ttl_sec: int = _DEFAULT_TTL_SEC) -> None:
        self.path = Path(path)
        self.ttl_sec = max(0, int(ttl_sec))
        self._searches: dict[str, dict[str, Any]] = {}
        self._details: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.hits = {"search": 0, "detail": 0}
        self.misses = {"search": 0, "detail": 0}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(blob, dict):
            return
        for k, v in (blob.get("searches") or {}).items():
            if isinstance(v, dict) and isinstance(v.get("items"), list):
                self._searches[str(k)] = v
        for k, v in (blob.get("details") or {}).items():
            if isinstance(v, dict):
                self._details[str(k)] = v

    def _is_fresh(self, entry: dict[str, Any]) -> bool:
        if self.ttl_sec <= 0:
            return True
        ts = float(entry.get("fetched_at") or 0)
        return (time.time() - ts) <= self.ttl_sec

    def get_search(self, keyword: str) -> list[dict[str, Any]] | None:
        key = normalize_keyword(keyword)
        if not key:
            return None
        with self._lock:
            entry = self._searches.get(key)
            if not entry or not self._is_fresh(entry):
                self.misses["search"] += 1
                return None
            items = entry.get("items")
            if not isinstance(items, list) or not items:
                self.misses["search"] += 1
                return None
            self.hits["search"] += 1
            return [dict(it) for it in items if isinstance(it, dict)]

    def put_search(self, keyword: str, items: list[dict[str, Any]]) -> None:
        key = normalize_keyword(keyword)
        if not key or not items:
            return
        slim = [
            {
                "note_id": str(it.get("note_id") or ""),
                "title": str(it.get("title") or ""),
                "content": str(it.get("content") or ""),
                "author": str(it.get("author") or ""),
                "liked_count": it.get("liked_count"),
                "collected_count": it.get("collected_count"),
                "comments_count": it.get("comments_count"),
                "image_urls": list(it.get("image_urls") or []),
            }
            for it in items
            if isinstance(it, dict) and it.get("note_id")
        ]
        if not slim:
            return
        with self._lock:
            self._searches[key] = {"items": slim, "fetched_at": time.time()}
            self._dirty = True

    def get_detail(self, note_id: str) -> dict[str, Any] | None:
        nid = str(note_id or "").strip()
        if not nid:
            return None
        with self._lock:
            entry = self._details.get(nid)
            if not entry or not self._is_fresh(entry):
                self.misses["detail"] += 1
                return None
            self.hits["detail"] += 1
            return dict(entry)

    def put_detail(self, note_id: str, payload: dict[str, Any]) -> None:
        nid = str(note_id or "").strip()
        if not nid or not isinstance(payload, dict):
            return
        with self._lock:
            self._details[nid] = {
                "payload": payload,
                "fetched_at": time.time(),
            }
            self._dirty = True

    def get_detail_payload(self, note_id: str) -> dict[str, Any] | None:
        entry = self.get_detail(note_id)
        if not entry:
            return None
        payload = entry.get("payload")
        return payload if isinstance(payload, dict) else None

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            snap_search = dict(self._searches)
            snap_detail = dict(self._details)
            self._dirty = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"searches": snap_search, "details": snap_detail}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def stats_line(self) -> str:
        with self._lock:
            h_s, h_d = self.hits["search"], self.hits["detail"]
            m_s, m_d = self.misses["search"], self.misses["detail"]
        return (
            f"xhs-cache hits search={h_s} detail={h_d}; "
            f"miss search={m_s} detail={m_d}"
        )


_active: XhsNoteCache | None = None


def set_active_cache(cache: XhsNoteCache | None) -> None:
    global _active
    _active = cache


def get_active_cache() -> XhsNoteCache | None:
    return _active


def cache_path_for_trip(trip_path: Path) -> Path:
    """与 ``fill_xhs_images.py`` 的 ``sources/xiaohongshu-image-sources.json`` 同目录。"""
    shared_root = os.environ.get("ROADBOOK_XHS_CACHE_ROOT", "").strip()
    if shared_root:
        return Path(shared_root).expanduser().resolve() / "xhs-note-cache.json"
    return Path(trip_path).resolve().parent / "sources" / "xhs-note-cache.json"
