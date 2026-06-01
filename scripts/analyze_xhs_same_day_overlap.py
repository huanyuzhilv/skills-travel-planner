#!/usr/bin/env python3
"""验证「同一天不同配图槽 TikHub 检索结果是否高度相似」。

两层验证（建议先做离线，再抽样 --live）：

1. **离线（默认，不扣费）**：对比同一天各槽 ``planned_search_keywords`` 与首词相似度。
2. **在线（--live）**：对每槽首词调 ``search_notes``，算笔记 ID / 图片指纹 两两重叠率。

用法:
  python3 scripts/analyze_xhs_same_day_overlap.py path/to/tripData.json
  python3 scripts/analyze_xhs_same_day_overlap.py path/to/tripData.json --live
  python3 scripts/analyze_xhs_same_day_overlap.py path/to/tripData.json --live --simulate-fill

输出：终端摘要 + JSON 报告（默认 ``<路书目录>/sources/xhs-same-day-overlap-report.json``）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from brand_logo import is_cover_brand_logo_slot  # noqa: E402
from repo_dotenv import load_repo_dotenv  # noqa: E402
from xhs_image_url_rules import fingerprint_image_url, normalize_xhs_image_url  # noqa: E402
from xhs_search_keyword_rules import (  # noqa: E402
    classify_image_slot,
    component_index_from_path,
    daily_data_for_path,
    get_component,
    planned_search_keywords,
)


def _token_set(text: str) -> set[str]:
    import re

    return {t for t in re.split(r"\s+", (text or "").strip()) if t}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collect_image_slots(data: dict[str, Any]) -> list[dict[str, Any]]:
    """收集可分析配图槽（含无 slotLabel 的 daily 图，用 path 合成 label）。"""

    def walk(node: Any, path: list[Any]) -> None:
        if isinstance(node, dict):
            has_img = any(k in node for k in ("url", "alternates"))
            label = node.get("slotLabel")
            if has_img and isinstance(label, str) and label.strip():
                out.append({"path": list(path), "label": label.strip(), "has_slot_label": True})
            elif has_img and _is_daily_image_path(data, path):
                syn = _synthetic_daily_label(data, path)
                if syn:
                    out.append({"path": list(path), "label": syn, "has_slot_label": False})
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [i])

    out: list[dict[str, Any]] = []
    walk(data, [])
    return out


def _is_daily_image_path(data: dict[str, Any], path: list[Any]) -> bool:
    ci = component_index_from_path(path)
    if ci is None:
        return False
    comp = get_component(data, ci)
    if not isinstance(comp, dict) or comp.get("type") != "daily":
        return False
    try:
        di = path.index("data")
        if di + 1 < len(path):
            return path[di + 1] in (
                "backgroundImage",
                "sideImage",
                "topImages",
                "bottomImages",
            )
    except ValueError:
        pass
    return False


def _synthetic_daily_label(data: dict[str, Any], path: list[Any]) -> str:
    dd = daily_data_for_path(data, path) or {}
    theme = str(dd.get("theme") or "").strip()
    try:
        di = path.index("data")
        field = str(path[di + 1]) if di + 1 < len(path) else "image"
        if field in ("topImages", "bottomImages") and di + 2 < len(path):
            field = f"{field}[{path[di + 2]}]"
    except ValueError:
        field = "image"
    if theme:
        return f"{theme} · {field}"
    return field


def daily_group_key(data: dict[str, Any], path: list[Any]) -> str | None:
    ci = component_index_from_path(path)
    if ci is None:
        return None
    comp = get_component(data, ci)
    if not isinstance(comp, dict) or comp.get("type") != "daily":
        return None
    dd = comp.get("data") if isinstance(comp.get("data"), dict) else {}
    date = str(dd.get("date") or "").strip()
    theme = str(dd.get("theme") or "").strip()
    comp_id = str(comp.get("id") or ci)
    return f"daily:{comp_id}|{date}|{theme}"


def should_skip_slot(data: dict[str, Any], path: list[Any]) -> tuple[bool, str]:
    if is_cover_brand_logo_slot(path):
        return True, "cover_logo"
    from fill_xhs_images import (  # noqa: WPS433
        is_fee_or_service_text_block_image_slot,
        is_flyai_transport_only_slot,
        should_skip_hotel_feature_imagery,
    )

    if should_skip_hotel_feature_imagery(data, path):
        return True, "empty_hotel"
    if is_fee_or_service_text_block_image_slot(data, path):
        return True, "fee_service"
    kind = classify_image_slot(data, path)
    if is_flyai_transport_only_slot(data, path, kind):
        return True, "transport_flyai"
    return False, ""


@dataclass
class SlotAnalysis:
    path: str
    label: str
    slot_kind: str
    primary_keyword: str
    keywords: list[str]
    skip: bool = False
    skip_reason: str = ""
    search_note_ids: list[str] = field(default_factory=list)
    search_image_fps: list[str] = field(default_factory=list)
    simulated_fill_urls: list[str] = field(default_factory=list)
    simulated_fill_fps: list[str] = field(default_factory=list)


@dataclass
class PairOverlap:
    slot_a: str
    slot_b: str
    primary_kw_identical: bool
    keyword_list_jaccard: float
    primary_token_jaccard: float
    note_id_jaccard: float | None = None
    image_fp_jaccard: float | None = None
    simulated_fill_fp_jaccard: float | None = None
    shared_note_ids: list[str] = field(default_factory=list)
    shared_image_fps: list[str] = field(default_factory=list)


def analyze_keywords(data: dict[str, Any], slots: list[SlotAnalysis]) -> list[PairOverlap]:
    pairs: list[PairOverlap] = []
    for a, b in combinations(slots, 2):
        ka, kb = set(a.keywords), set(b.keywords)
        pairs.append(
            PairOverlap(
                slot_a=a.label,
                slot_b=b.label,
                primary_kw_identical=a.primary_keyword == b.primary_keyword and bool(a.primary_keyword),
                keyword_list_jaccard=round(jaccard(ka, kb), 4),
                primary_token_jaccard=round(
                    jaccard(_token_set(a.primary_keyword), _token_set(b.primary_keyword)), 4
                ),
            )
        )
    return pairs


def live_search_slot(
    root: Path,
    slot: SlotAnalysis,
    *,
    top_notes: int,
    cooldown_ms: int,
) -> None:
    from tikhub_xhs_client import TikHubXhsClient, parse_search_results

    if slot.skip or not slot.primary_keyword:
        return
    client = TikHubXhsClient(timeout=60.0)
    raw = client.search_notes(slot.primary_keyword)
    if cooldown_ms > 0:
        time.sleep(cooldown_ms / 1000.0)
    feeds = parse_search_results(raw)[:top_notes]
    note_ids: list[str] = []
    fps: list[str] = []
    seen_fp: set[str] = set()
    for item in feeds:
        nid = str(item.get("note_id") or "").strip()
        if nid:
            note_ids.append(nid)
        for u in item.get("image_urls") or []:
            nu = normalize_xhs_image_url(str(u))
            fp = fingerprint_image_url(nu)
            if fp and fp not in seen_fp:
                seen_fp.add(fp)
                fps.append(fp)
    slot.search_note_ids = note_ids
    slot.search_image_fps = fps


def simulate_fill_slot(root: Path, slot: SlotAnalysis, *, timeout_ms: int, cooldown_ms: int) -> None:
    from fill_xhs_images import fetch_slot_images

    if slot.skip or not slot.primary_keyword:
        return
    urls, _ = fetch_slot_images(
        root,
        slot.primary_keyword,
        4,
        4,
        timeout_ms,
        retries=1,
        cooldown_ms=cooldown_ms,
        max_feed_details=6,
        exclude_fingerprints=None,
        max_images_per_feed=5,
    )
    fps: list[str] = []
    seen: set[str] = set()
    for u in urls:
        fp = fingerprint_image_url(normalize_xhs_image_url(u))
        if fp and fp not in seen:
            seen.add(fp)
            fps.append(fp)
    slot.simulated_fill_urls = urls
    slot.simulated_fill_fps = fps


def enrich_live_overlaps(pairs: list[PairOverlap], slots: list[SlotAnalysis]) -> None:
    by_label = {s.label: s for s in slots}
    for p in pairs:
        a, b = by_label.get(p.slot_a), by_label.get(p.slot_b)
        if not a or not b:
            continue
        na, nb = set(a.search_note_ids), set(b.search_note_ids)
        fa, fb = set(a.search_image_fps), set(b.search_image_fps)
        sa, sb = set(a.simulated_fill_fps), set(b.simulated_fill_fps)
        if na or nb:
            p.note_id_jaccard = round(jaccard(na, nb), 4)
            p.shared_note_ids = sorted(na & nb)
        if fa or fb:
            p.image_fp_jaccard = round(jaccard(fa, fb), 4)
            p.shared_image_fps = sorted(fa & fb)[:20]
        if sa or sb:
            p.simulated_fill_fp_jaccard = round(jaccard(sa, sb), 4)


def load_env_for_tikhub() -> None:
    load_repo_dotenv(_REPO_ROOT)


def summarize_day(day_key: str, slots: list[SlotAnalysis], pairs: list[PairOverlap]) -> dict[str, Any]:
    active = [s for s in slots if not s.skip]
    kw_pairs = pairs
    identical_primary = sum(1 for p in kw_pairs if p.primary_kw_identical)
    avg_kw_j = sum(p.keyword_list_jaccard for p in kw_pairs) / len(kw_pairs) if kw_pairs else 0.0
    avg_tok_j = sum(p.primary_token_jaccard for p in kw_pairs) / len(kw_pairs) if kw_pairs else 0.0

    note_js = [p.note_id_jaccard for p in pairs if p.note_id_jaccard is not None]
    img_js = [p.image_fp_jaccard for p in pairs if p.image_fp_jaccard is not None]
    fill_js = [p.simulated_fill_fp_jaccard for p in pairs if p.simulated_fill_fp_jaccard is not None]

    unique_primary = len({s.primary_keyword for s in active if s.primary_keyword})
    verdict = "unknown"
    if note_js:
        mx = max(note_js)
        if mx >= 0.5:
            verdict = "high_overlap_likely"
        elif mx >= 0.25:
            verdict = "moderate_overlap"
        else:
            verdict = "low_overlap"
    elif kw_pairs:
        if identical_primary >= len(kw_pairs) * 0.5 or avg_kw_j >= 0.6:
            verdict = "keyword_collision_risk"
        elif avg_kw_j >= 0.35:
            verdict = "keyword_similar_moderate"
        else:
            verdict = "keywords_diverse"

    parts = day_key.split("|", 2)
    return {
        "day_key": day_key,
        "date": parts[1] if len(parts) > 1 else "",
        "theme": parts[2] if len(parts) > 2 else "",
        "slot_count": len(slots),
        "active_xhs_slots": len(active),
        "unique_primary_keywords": unique_primary,
        "pair_count": len(pairs),
        "identical_primary_keyword_pairs": identical_primary,
        "avg_keyword_list_jaccard": round(avg_kw_j, 4),
        "avg_primary_token_jaccard": round(avg_tok_j, 4),
        "max_note_id_jaccard": round(max(note_js), 4) if note_js else None,
        "avg_note_id_jaccard": round(sum(note_js) / len(note_js), 4) if note_js else None,
        "max_image_fp_jaccard": round(max(img_js), 4) if img_js else None,
        "max_simulated_fill_fp_jaccard": round(max(fill_js), 4) if fill_js else None,
        "verdict": verdict,
    }


def print_report(report: dict[str, Any]) -> None:
    print("\n=== 同一天配图槽 TikHub 重叠分析 ===\n")
    for day in report["days"]:
        print(f"📅 {day.get('date') or day['day_key']}  theme={day.get('theme') or '-'}")
        print(
            f"   槽位: {day['active_xhs_slots']}/{day['slot_count']} 走小红书 | "
            f"首词种类: {day['unique_primary_keywords']}"
        )
        print(
            f"   关键词: 首词完全相同对数={day['identical_primary_keyword_pairs']}/{day['pair_count']} | "
            f"关键词列表 Jaccard 均值={day['avg_keyword_list_jaccard']:.2%} | "
            f"首词 token Jaccard 均值={day['avg_primary_token_jaccard']:.2%}"
        )
        if day.get("max_note_id_jaccard") is not None:
            print(
                f"   在线 search_notes: 笔记 ID Jaccard 最大={day['max_note_id_jaccard']:.2%} "
                f"均值={day.get('avg_note_id_jaccard', 0):.2%} | "
                f"缩略图指纹 Jaccard 最大={day.get('max_image_fp_jaccard', 0):.2%}"
            )
        if day.get("max_simulated_fill_fp_jaccard") is not None:
            print(
                f"   模拟 fill(4张/槽): 成稿指纹 Jaccard 最大={day['max_simulated_fill_fp_jaccard']:.2%}"
            )
        print(f"   判定: {day['verdict']}\n")

        for slot in day["slots"]:
            if slot.get("skip"):
                print(f"     - [skip:{slot['skip_reason']}] {slot['label']}")
                continue
            print(
                f"     - [{slot['slot_kind']}] {slot['label']}\n"
                f"       首词: {slot['primary_keyword']!r}  (共 {len(slot['keywords'])} 个变体)"
            )

        hot = sorted(
            day.get("pairs", []),
            key=lambda p: (
                p.get("note_id_jaccard") or p.get("keyword_list_jaccard") or 0
            ),
            reverse=True,
        )[:5]
        if hot:
            print("   重叠最高 5 对:")
            for p in hot:
                extra = ""
                if p.get("note_id_jaccard") is not None:
                    extra = (
                        f" note_j={p['note_id_jaccard']:.2%}"
                        f" img_j={p.get('image_fp_jaccard', 0):.2%}"
                    )
                    if p.get("simulated_fill_fp_jaccard") is not None:
                        extra += f" fill_j={p['simulated_fill_fp_jaccard']:.2%}"
                print(
                    f"     · {p['slot_a']} ↔ {p['slot_b']}: "
                    f"首词相同={p['primary_kw_identical']} kw_j={p['keyword_list_jaccard']:.2%}{extra}"
                )
                if p.get("shared_note_ids"):
                    print(f"       共有笔记: {', '.join(p['shared_note_ids'][:8])}")
        print()

    print("--- 解读 ---")
    print("· keyword_collision_risk：多数槽首词相同或关键词列表高度重叠 → 猜测很可能成立")
    print("· high_overlap_likely：在线搜索笔记 ID 重叠 ≥50% → 猜测已证实")
    print("· 生产 fill 有跨槽指纹去重 + 多关键词轮换；模拟 fill 未去重，数值偏悲观上限")
    print(f"完整 JSON: {report['output_path']}\n")


def build_slot_analyses(data: dict[str, Any], raw_slots: list[dict[str, Any]]) -> list[SlotAnalysis]:
    out: list[SlotAnalysis] = []
    for s in raw_slots:
        path = s["path"]
        label = s["label"]
        skip, reason = should_skip_slot(data, path)
        kind = classify_image_slot(data, path)
        kws = planned_search_keywords(data, path, label) if not skip else []
        out.append(
            SlotAnalysis(
                path="/".join(map(str, path)),
                label=label,
                slot_kind=kind,
                primary_keyword=kws[0] if kws else "",
                keywords=kws,
                skip=skip,
                skip_reason=reason,
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="验证同一天 TikHub 配图槽检索重叠")
    ap.add_argument("trip_json", help="tripData.json 路径")
    ap.add_argument(
        "--live",
        action="store_true",
        help="调用 TikHub search_notes（按槽首词，会扣费）",
    )
    ap.add_argument(
        "--simulate-fill",
        action="store_true",
        help="额外模拟 fill_xhs 单槽取 4 张（每槽至少 1 次 search + 可能 get_note_info，扣费更多）",
    )
    ap.add_argument("--top-notes", type=int, default=15, help="live 模式每条首词保留前 N 篇笔记")
    ap.add_argument("--cooldown-ms", type=int, default=800, help="live 请求间隔毫秒")
    ap.add_argument("--timeout-ms", type=int, default=120000, help="simulate-fill 超时")
    ap.add_argument("-o", "--output", type=Path, default=None, help="JSON 报告路径")
    ap.add_argument("--day", type=int, default=0, help="仅分析第 N 个 daily（1-based），0=全部")
    args = ap.parse_args()

    root = _REPO_ROOT
    trip_path = Path(args.trip_json).resolve()
    data = json.loads(trip_path.read_text(encoding="utf-8"))

    if args.live or args.simulate_fill:
        load_env_for_tikhub()

    raw_slots = collect_image_slots(data)
    slots = build_slot_analyses(data, raw_slots)

    groups: dict[str, list[SlotAnalysis]] = {}
    for slot in slots:
        path_parts = slot.path.split("/")
        # re-parse path for grouping
        path_list: list[Any] = []
        for p in path_parts:
            path_list.append(int(p) if p.isdigit() else p)
        gk = daily_group_key(data, path_list)
        if gk:
            groups.setdefault(gk, []).append(slot)

    if args.day > 0:
        keys = sorted(groups.keys())
        if args.day > len(keys):
            raise SystemExit(f"--day {args.day} 超出 daily 数量 {len(keys)}")
        groups = {keys[args.day - 1]: groups[keys[args.day - 1]]}

    days_out: list[dict[str, Any]] = []
    for day_key in sorted(groups.keys()):
        day_slots = groups[day_key]
        pairs = analyze_keywords(data, [s for s in day_slots if not s.skip])

        if args.live:
            from tikhub_xhs_feeds import ensure_tikhub_api_key

            ensure_tikhub_api_key(_REPO_ROOT)
            load_env_for_tikhub()
            seen_kw: dict[str, SlotAnalysis] = {}
            for slot in day_slots:
                if slot.skip or not slot.primary_keyword:
                    continue
                kw = slot.primary_keyword
                if kw in seen_kw:
                    donor = seen_kw[kw]
                    slot.search_note_ids = list(donor.search_note_ids)
                    slot.search_image_fps = list(donor.search_image_fps)
                    continue
                live_search_slot(
                    root,
                    slot,
                    top_notes=args.top_notes,
                    cooldown_ms=args.cooldown_ms,
                )
                seen_kw[kw] = slot

        if args.simulate_fill:
            for slot in day_slots:
                if slot.skip:
                    continue
                simulate_fill_slot(
                    root,
                    slot,
                    timeout_ms=args.timeout_ms,
                    cooldown_ms=args.cooldown_ms,
                )

        enrich_live_overlaps(pairs, day_slots)

        # fix summarize day theme - parse from day_key
        summary = summarize_day(day_key, day_slots, pairs)
        days_out.append(
            {
                **summary,
                "slots": [asdict(s) for s in day_slots],
                "pairs": [asdict(p) for p in pairs],
            }
        )

    out_path = args.output or (trip_path.parent / "sources" / "xhs-same-day-overlap-report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "trip_json": str(trip_path),
        "mode": {
            "live": args.live,
            "simulate_fill": args.simulate_fill,
            "top_notes": args.top_notes,
        },
        "days": days_out,
        "output_path": str(out_path),
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_report({**report, "output_path": str(out_path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
