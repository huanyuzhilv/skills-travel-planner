"""路书 v2 文案软上限告警（不修改数据）：导出/校验时打印 WARN，减轻 PDF 单页撑爆。"""

from __future__ import annotations

import re
from typing import Any

_LIMITS = {
    "daily_overview": 900,
    "daily_text": 2500,
    "daily_item_note": 400,
    "feature_intro": 1200,
    "feature_desc": 2000,
    "textblock_content_plain": 8000,
    "highlights_content": 1200,
    "highlights_item": 200,
}

_TAG_RE = re.compile(r"<[^>]+>")


def _text_len(obj: Any) -> int:
    return len(str(obj or "").strip())


def _strip_html(s: str) -> str:
    t = _TAG_RE.sub(" ", str(s or ""))
    return " ".join(t.split())


def warn_oversized_fields(trip_data: dict[str, Any], *, label: str = "tripData") -> int:
    """返回 WARN 条数。"""
    meta = trip_data.get("meta") or {}
    if meta.get("version") != "2.0":
        return 0
    comps = trip_data.get("components")
    if not isinstance(comps, list):
        return 0

    n = 0
    for ci, comp in enumerate(comps):
        if not isinstance(comp, dict):
            continue
        data = comp.get("data") if isinstance(comp.get("data"), dict) else {}
        ctype = str(comp.get("type") or "")
        cid = str(comp.get("id") or ci)

        if ctype == "daily":
            for key, lim_key in (("overview", "daily_overview"), ("text", "daily_text"), ("description", "daily_text")):
                raw = _text_len(data.get(key))
                lim = _LIMITS[lim_key]
                if raw > lim:
                    print(
                        f"  WARNING [{label}] 组件 #{ci} ({cid}) daily.{key} 约 {raw} 字，建议 ≤ {lim}（印刷分页）",
                        flush=True,
                    )
                    n += 1
            items = data.get("items")
            if isinstance(items, list):
                for ii, it in enumerate(items):
                    if not isinstance(it, dict):
                        continue
                    for fk in ("note", "notes", "description", "tips"):
                        raw = _text_len(it.get(fk))
                        if raw > _LIMITS["daily_item_note"]:
                            print(
                                f"  WARNING [{label}] 组件 #{ci} daily.items[{ii}].{fk} 约 {raw} 字，建议 ≤ {_LIMITS['daily_item_note']}",
                                flush=True,
                            )
                            n += 1

        elif ctype == "feature":
            for key, lim_key in (
                ("introduction", "feature_intro"),
                ("description", "feature_desc"),
                ("copy", "feature_desc"),
            ):
                raw = _text_len(data.get(key))
                lim = _LIMITS[lim_key]
                if raw > lim:
                    print(
                        f"  WARNING [{label}] 组件 #{ci} feature.{key} 约 {raw} 字，建议 ≤ {lim}",
                        flush=True,
                    )
                    n += 1

        elif ctype == "text-block":
            plain = _strip_html(str(data.get("content") or ""))
            raw = len(plain)
            lim = _LIMITS["textblock_content_plain"]
            if raw > lim:
                print(
                    f"  WARNING [{label}] 组件 #{ci} text-block 正文（去标签）约 {raw} 字，建议 ≤ {lim}",
                    flush=True,
                )
                n += 1

        elif ctype == "highlights":
            raw = _text_len(data.get("content"))
            if raw > _LIMITS["highlights_content"]:
                print(
                    f"  WARNING [{label}] 组件 #{ci} highlights.content 约 {raw} 字，建议 ≤ {_LIMITS['highlights_content']}",
                    flush=True,
                )
                n += 1
            items = data.get("items")
            if isinstance(items, list):
                for ii, line in enumerate(items):
                    raw = _text_len(line)
                    if raw > _LIMITS["highlights_item"]:
                        print(
                            f"  WARNING [{label}] 组件 #{ci} highlights.items[{ii}] 约 {raw} 字",
                            flush=True,
                        )
                        n += 1

    return n
