"""小红书配图门禁：远程 URL 判定（与 fill_xhs / validate 共用，避免漂移）。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_HTTPS_IMG = re.compile(r"^https://", re.I)


def normalize_xhs_image_url(url: str) -> str:
    """小红书 MCP 常见返回 http://*.xhscdn.com；浏览器与交付门禁要求 https。"""
    u = str(url or "").strip()
    if not u:
        return u
    low = u.lower()
    if low.startswith("http://") and (
        "xhscdn.com" in low or "xhscdn.net" in low or "rednotecdn.com" in low
    ):
        return "https://" + u[7:]
    return u


def is_remote_https_image_url(url: str) -> bool:
    """交付级配图须为 https 远端 URL（小红书 MCP 返回通常为 xhscdn 等）。"""
    u = str(url or "").strip()
    if not u:
        return False
    if not _HTTPS_IMG.match(u):
        return False
    low = u.lower()
    if low.endswith((".mp4", ".mov", ".m3u8")):
        return False
    return True


def fingerprint_image_url(url: str) -> str:
    """同一配图在小红书 CDN 上常见多种 URL（``!nd_prv`` / ``!nd_dft``、尾缀格式等）；用指纹跨槽去重。

    非 xhscdn 的 https 图使用 ``host + path``（忽略 query）作兜底指纹。
    若无法解析则返回空串，调用方退化为仅原始 URL 去重。
    """
    u = normalize_xhs_image_url(str(url or "").strip())
    if not u:
        return ""
    p = urlparse(u)
    host = (p.netloc or "").lower()
    path = p.path or ""
    if "xhscdn.com" in host or "xhscdn.net" in host or "rednotecdn.com" in host:
        seg = path.rstrip("/").split("/")[-1] if path else ""
        if "!" in seg:
            base = seg.split("!", 1)[0].strip()
            if len(base) >= 6:
                return f"xhs:{base.lower()}"
        m = re.search(r"/avatar/([^/]+?)(?:\.[a-z0-9]+)?$", path, re.I)
        if m:
            return f"xhsav:{m.group(1).lower()}"
        if seg:
            return f"xhsp:{path.lower()}"
    path_l = path.lower()
    if path_l:
        return f"web:{host}{path_l}"
    return ""


def dedupe_urls_by_fingerprint(
    urls: list[str],
    *,
    exclude_fingerprints: set[str] | None = None,
) -> list[str]:
    """保序：同一指纹只保留首条 URL；可选排除已在全球见过的指纹。"""
    ex = exclude_fingerprints or set()
    out: list[str] = []
    seen_fp: set[str] = set()
    seen_raw: set[str] = set()
    for raw in urls:
        u = normalize_xhs_image_url(str(raw or "").strip())
        if not u or u in seen_raw:
            continue
        fp = fingerprint_image_url(u)
        if fp:
            if fp in ex or fp in seen_fp:
                continue
            seen_fp.add(fp)
        seen_raw.add(u)
        out.append(u)
    return out


def register_url_fingerprints(urls: list[str], registry: set[str]) -> None:
    for raw in urls:
        u = normalize_xhs_image_url(str(raw or "").strip())
        if not u:
            continue
        fp = fingerprint_image_url(u)
        if fp:
            registry.add(fp)
