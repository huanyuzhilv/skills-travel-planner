"""远程图片下载 + 感知哈希（pHash）；用于可选视觉去重。"""

from __future__ import annotations

import io
import os
import urllib.request
from typing import Any, Tuple

# 懒加载：未安装 Pillow/ImageHash 时仅在使用时报错


def _ensure_imagehash() -> tuple[Any, Any]:
    try:
        from PIL import Image  # noqa: WPS433
        import imagehash  # noqa: WPS433
    except ImportError as e:
        raise RuntimeError(
            "视觉去重需要 Pillow 与 ImageHash：pip install -r requirements-roadbook-images.txt"
        ) from e
    return Image, imagehash


def download_image_bytes(url: str, timeout_s: float = 25.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "RoadbookImageEngine/1.0 (+https://local)"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def compute_phash_hex(url: str, *, timeout_s: float = 25.0) -> Tuple[str, int, int]:
    """返回 (phash_hex, width, height)。"""
    Image, imagehash = _ensure_imagehash()
    raw = download_image_bytes(url, timeout_s=timeout_s)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    h_obj = imagehash.phash(img)
    return str(h_obj), w, h


def phash_distance_hex(a_hex: str, b_hex: str) -> int:
    """汉明距离；无法在旧缓存上解析时返回大值。"""
    _, imagehash = _ensure_imagehash()
    try:
        h1 = imagehash.hex_to_hash(a_hex)
        h2 = imagehash.hex_to_hash(b_hex)
    except Exception:
        return 999
    return int(h1 - h2)


def is_similar_phash_hex(a_hex: str, b_hex: str, *, max_distance: int | None = None) -> bool:
    mx = max_distance
    if mx is None:
        raw = os.environ.get("ROADBOOK_VDEDUP_PHASH_MAX", "").strip()
        mx = int(raw) if raw.isdigit() else 6
    return phash_distance_hex(a_hex, b_hex) <= mx
