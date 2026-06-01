"""路书 v2 印刷预分页（启发式）：估算组件垂直占用，曾用于写入 meta.printLayout.breakBeforeIndices。

当前 roadbook-v2 模板已不再根据该字段插入 `.print-page-break-before`（打印由浏览器自然分页）。
本模块仍可由 generate.py 调用以写入 meta 供未来扩展或外部工具使用。
手动关闭：tripData.meta.printLayout.disableAuto = true
"""

from __future__ import annotations

from typing import Any

# A4 可印高度（mm）：与模板 @page margin 10mm 对齐 → 内容区约 277mm
_A4_CONTENT_MM = 277.0
# CSS px 折算（96dpi 约定，与 Chromium 打印缩放大致可比）
_MM_TO_PX = 96.0 / 25.4
_PAGE_BUDGET_PX = _A4_CONTENT_MM * _MM_TO_PX
# 累计「估算高度」允许堆叠的虚拟页数，再建议在下一块根组件前强分新页（略大于 1，避免略超 A4 就频繁打断）
_VIRTUAL_PAGE_STACK = 2.35



def _len(obj: Any) -> int:
    return len(str(obj or "").strip())


def _estimate_feature_px(data: dict[str, Any]) -> float:
    st = str(data.get("subtype") or "")
    cells = data.get("cells") or data.get("gallery") or []
    ncell = len(cells) if isinstance(cells, list) else 0
    base = 620.0 if "hotel" in st else 520.0
    body = _len(data.get("introduction")) + _len(data.get("description")) + _len(data.get("copy"))
    return base + min(900.0, body * 0.12) + ncell * 95.0


def _estimate_daily_px(data: dict[str, Any]) -> float:
    items = data.get("items") or []
    n = len(items) if isinstance(items, list) else 0
    top_slots = data.get("topImages") or data.get("topImageSlots")
    if not isinstance(top_slots, list):
        top_slots = []
    nt = min(len(top_slots), 4)
    overview = _len(data.get("overview")) + _len(data.get("text")) + _len(data.get("description"))
    side = data.get("sideImage") or data.get("sideImages")
    nside = len(side) if isinstance(side, list) else (1 if side else 0)
    nside = min(nside, 8)
    return (
        480.0
        + n * 100.0
        + nt * 140.0
        + nside * 85.0
        + min(650.0, overview * 0.25)
    )


def estimate_component_px(comp: dict[str, Any]) -> float:
    """单组件估算高度（封顶约一页）：内部多块内容往往已有 CSS 分页。"""
    data = comp.get("data") if isinstance(comp.get("data"), dict) else {}
    t = str(comp.get("type") or "")
    if t == "highlights":
        d = data
        items = d.get("items") if isinstance(d.get("items"), list) else []
        raw = 760.0 + len(items) * 36.0 + min(500.0, _len(d.get("content")) * 0.15)
    elif t == "itinerary":
        d = data
        style = str(d.get("style") or "")
        items = d.get("items") if isinstance(d.get("items"), list) else []
        row = 720.0 + len(items) * 95.0
        raw = row + (80.0 if style == "timeline" else 0.0)
    elif t == "daily":
        raw = _estimate_daily_px(data)
    elif t == "feature":
        raw = _estimate_feature_px(data)
    elif t == "text-block":
        content = _len(data.get("content"))
        raw = 420.0 + min(1200.0, content * 0.08)
    else:
        raw = 560.0
    cap = _PAGE_BUDGET_PX * 1.12
    return min(cap, raw)


def compute_break_indices(components: list[dict[str, Any]]) -> list[int]:
    budget = _PAGE_BUDGET_PX * _VIRTUAL_PAGE_STACK
    acc = 0.0
    out: list[int] = []
    for i, comp in enumerate(components):
        if not isinstance(comp, dict):
            continue
        h = estimate_component_px(comp)
        if i == 0:
            acc = h
            continue
        if acc + h > budget:
            out.append(i)
            acc = h
        else:
            acc += h
    return out


def apply_print_layout_hints(trip_data: dict[str, Any]) -> bool:
    """写入 tripData.meta.printLayout；若跳过则返回 False。"""
    meta = trip_data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        trip_data["meta"] = meta
    pl_existing = meta.get("printLayout")
    if isinstance(pl_existing, dict) and pl_existing.get("disableAuto"):
        return False
    comps = trip_data.get("components")
    if not isinstance(comps, list) or not comps:
        return False
    if meta.get("version") != "2.0":
        return False

    breaks = [i for i in compute_break_indices(comps) if i > 0]
    pl = meta.setdefault("printLayout", {})
    pl["version"] = 1
    pl["breakBeforeIndices"] = breaks
    pl["pageBudgetPx"] = round(_PAGE_BUDGET_PX, 1)
    return True
