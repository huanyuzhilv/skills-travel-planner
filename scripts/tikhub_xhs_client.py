"""TikHub 小红书 REST 客户端（api.tikhub.io）。

**性能优化**：模块级共享 ``httpx.Client``（thread-safe、keep-alive），避免每次请求
重新 TLS 握手。多个 ``TikHubXhsClient`` 实例与多线程并发场景下复用同一池，
显著降低 ``fill_xhs_images.py`` 在并发拉图时的总耗时。

如需在主进程退出时清理连接池，调用模块级 ``close_shared_client()``。
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError as exc:
    raise SystemExit("请先安装: pip3 install httpx") from exc

API_BASE = os.getenv("TIKHUB_API_BASE", "https://api.tikhub.io").rstrip("/")

_CLIENT_LOCK = threading.Lock()
_SHARED_CLIENT: Optional[httpx.Client] = None
_SHARED_CLIENT_TIMEOUT: float = 0.0


def require_api_key() -> str:
    key = (os.getenv("TIKHUB_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("未设置 TIKHUB_API_KEY（仓库根 .env 或环境变量）")
    return key


def _get_shared_client(timeout: float) -> httpx.Client:
    """惰性创建/复用模块级 httpx.Client（keep-alive + thread-safe）。

    若当前共享 Client 的超时小于本次请求需求，则重建以满足最严格调用方；
    httpx.Client 本身是线程安全的，可在多线程间共享。
    """
    global _SHARED_CLIENT, _SHARED_CLIENT_TIMEOUT
    with _CLIENT_LOCK:
        if _SHARED_CLIENT is None or timeout > _SHARED_CLIENT_TIMEOUT:
            if _SHARED_CLIENT is not None:
                try:
                    _SHARED_CLIENT.close()
                except Exception:  # noqa: BLE001
                    pass
            _SHARED_CLIENT = httpx.Client(
                timeout=timeout,
                verify=False,
                limits=httpx.Limits(
                    max_keepalive_connections=16,
                    max_connections=32,
                    keepalive_expiry=30.0,
                ),
                headers={"Accept": "application/json"},
            )
            _SHARED_CLIENT_TIMEOUT = timeout
        return _SHARED_CLIENT


def close_shared_client() -> None:
    """主进程退出时调用；安全可重入。"""
    global _SHARED_CLIENT, _SHARED_CLIENT_TIMEOUT
    with _CLIENT_LOCK:
        if _SHARED_CLIENT is not None:
            try:
                _SHARED_CLIENT.close()
            except Exception:  # noqa: BLE001
                pass
        _SHARED_CLIENT = None
        _SHARED_CLIENT_TIMEOUT = 0.0


atexit.register(close_shared_client)


class TikHubXhsClient:
    def __init__(self, *, api_key: Optional[str] = None, timeout: float = 60.0) -> None:
        self._api_key = (api_key or require_api_key()).strip()
        self._timeout = max(1.0, float(timeout))

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        q = {k: v for k, v in params.items() if v is not None and v != ""}
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        client = _get_shared_client(self._timeout)
        resp = client.get(url, headers=headers, params=q or None, timeout=self._timeout)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("code") not in (None, 200, "200"):
            msg = body.get("message_zh") or body.get("message") or str(body)
            raise RuntimeError(f"TikHub 业务错误: {msg}")
        return body if isinstance(body, dict) else {"data": body}

    def search_notes(
        self,
        keyword: str,
        *,
        page: int = 1,
        sort_type: Optional[str] = None,
        note_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        # app_v2/search_notes 自 2026-05 起常返回 400；app/search_notes 仍可用且响应结构兼容 parse_search_results
        return self._get(
            "/api/v1/xiaohongshu/app/search_notes",
            {
                "keyword": keyword.strip(),
                "page": page,
                "sort_type": sort_type or "general",
                "note_type": note_type or "不限",
            },
        )

    def get_note_info(self, note_id: str) -> Dict[str, Any]:
        """笔记详情（app/get_note_info，正文与图片较全）。"""
        return self._get(
            "/api/v1/xiaohongshu/app/get_note_info",
            {"note_id": note_id.strip()},
        )

    def get_note_info_v4(self, note_id: str) -> Dict[str, Any]:
        return self._get(
            "/api/v1/xiaohongshu/web/get_note_info_v4",
            {"note_id": note_id.strip()},
        )


def _dig_note(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 TikHub 嵌套响应中提取 note 对象。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("title") and (
        "desc" in payload
        or "images_list" in payload
        or "image_list" in payload
    ):
        return payload
    for v in payload.values():
        if isinstance(v, dict):
            found = _dig_note(v)
            if found:
                return found
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    if "note" in item and isinstance(item["note"], dict):
                        return item["note"]
                    found = _dig_note(item)
                    if found:
                        return found
    return None


def extract_image_urls(note: Dict[str, Any]) -> List[str]:
    """从 note 或 images_list 提取 https 图片 URL（去重、保序）。"""
    urls: List[str] = []
    seen: set[str] = set()

    def add(u: Optional[str]) -> None:
        if not u or not isinstance(u, str):
            return
        u = u.strip()
        if not u.startswith("http") or u in seen:
            return
        seen.add(u)
        urls.append(u)

    for img in note.get("images_list") or note.get("image_list") or []:
        if not isinstance(img, dict):
            continue
        large = img.get("url_size_large") or img.get("original")
        multi = img.get("url_multi_level")
        if isinstance(multi, dict):
            large = large or multi.get("high")
        if large:
            add(large)
            continue
        origin = img.get("origin_img")
        if isinstance(origin, dict):
            add(origin.get("url"))
        add(img.get("url"))

    return urls


def normalize_search_item(item: Dict[str, Any]) -> Dict[str, Any]:
    note = item.get("note") if isinstance(item.get("note"), dict) else item
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    return {
        "note_id": str(note.get("id") or note.get("note_id") or ""),
        "xsec_token": str(note.get("xsec_token") or ""),
        "title": str(note.get("title") or ""),
        "content": str(note.get("desc") or note.get("description") or ""),
        "author": str(user.get("nickname") or user.get("nick_name") or ""),
        "author_id": str(user.get("userid") or user.get("user_id") or ""),
        "liked_count": note.get("liked_count"),
        "collected_count": note.get("collected_count"),
        "comments_count": note.get("comments_count"),
        "type": str(note.get("type") or ""),
        "image_urls": extract_image_urls(note),
    }


def normalize_detail_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    note = _dig_note(payload.get("data") or payload) or {}
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    return {
        "note_id": str(note.get("id") or note.get("note_id") or ""),
        "title": str(note.get("title") or ""),
        "content": str(note.get("desc") or note.get("description") or ""),
        "author": str(user.get("nickname") or user.get("nick_name") or ""),
        "author_id": str(user.get("userid") or user.get("user_id") or ""),
        "liked_count": note.get("liked_count"),
        "collected_count": note.get("collected_count"),
        "comments_count": note.get("comments_count"),
        "type": str(note.get("type") or ""),
        "image_urls": extract_image_urls(note),
    }


def extract_image_urls_from_detail_payload(payload: Dict[str, Any]) -> List[str]:
    """从 ``get_note_info`` 等 TikHub 完整响应中提取图片 URL。"""
    note = _dig_note(payload.get("data") or payload)
    if note:
        return extract_image_urls(note)
    return []


def parse_search_results(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 search_notes 返回的笔记列表。"""
    data = payload.get("data") or {}
    if isinstance(data, dict):
        inner = data.get("data") or data
        if isinstance(inner, dict):
            items = inner.get("items") or []
        else:
            items = []
    else:
        items = []
    out: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(normalize_search_item(item))
    return out
