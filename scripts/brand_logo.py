"""封面固定品牌 Logo（玩点旅行 WD trip）：不参与小红书搜图与 https 远端校验。

源文件在仓库 ``assets/brand/wd-trip-logo.png``；``tripData.cover.logo`` 写入逻辑相对路径
``roadbook-images/logo-brand-wdtrip.png``；接入自有后端时可转存对象存储。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BRAND_LOGO_FILENAME = "logo-brand-wdtrip.png"


def brand_logo_source_path(repo_root: Path) -> Path:
    return repo_root / "assets" / "brand" / "wd-trip-logo.png"


def brand_logo_relative_url() -> str:
    return f"roadbook-images/{BRAND_LOGO_FILENAME}"


def is_cover_brand_logo_slot(path: list[Any]) -> bool:
    return len(path) == 2 and path[0] == "cover" and path[1] == "logo"


def patch_cover_logo_in_trip_data(data: dict[str, Any]) -> None:
    """写入 ``cover.logo``：逻辑占位路径，清空小红书备选。"""
    cover = data.get("cover")
    if not isinstance(cover, dict):
        return
    url = brand_logo_relative_url()
    slot_label = "品牌 LOGO"
    raw_logo = cover.get("logo")
    if isinstance(raw_logo, dict) and isinstance(raw_logo.get("slotLabel"), str) and raw_logo["slotLabel"].strip():
        slot_label = raw_logo["slotLabel"].strip()
    cover["logo"] = {"slotLabel": slot_label, "url": url, "alternates": []}
