#!/usr/bin/env python3
"""仅用飞猪 FlyAI ``keyword-search``（网络）重填 roadbook v2 交通 feature 用车配图。

与 ``fill_xhs_images`` 交通槽策略一致（交付流水线已内置）；本脚本供**单独重刷大巴图**。
不调用小红书 MCP；下载本地化需再跑 ``assets/generate.py --save-updated-json``。

用法:
  python3 scripts/refill_transport_images_from_flyai.py \\
    generated-roadbooks/某目录/tripData.json \\
    --min-images 4 --max-images 4
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from image_fallback_chain import flyai_transport_mainpics


def _pack_transport_images(urls: list[str], slot_label: str, *, visible_slots: int = 3) -> list[dict[str, Any]]:
    """交通卡右侧最多 3 格；每格带完整 alternates 供备选切换。"""
    alts = [u for u in urls if u]
    if not alts:
        return []
    slot = (slot_label or "旅游用车").strip()
    n = min(max(visible_slots, 1), 3)
    out: list[dict[str, Any]] = []
    for i in range(n):
        primary = alts[i % len(alts)]
        rotated = alts[i:] + alts[:i]
        out.append({"url": primary, "alternates": rotated, "slotLabel": slot})
    return out


def refill_transport_images(
    data: dict[str, Any],
    *,
    min_images: int,
    max_images: int,
    flyai_timeout: int,
    dry_run: bool,
) -> int:
    updated = 0
    comps = data.get("components")
    if not isinstance(comps, list):
        return 0

    for ci, comp in enumerate(comps):
        if not isinstance(comp, dict) or comp.get("type") != "feature":
            continue
        dd = comp.get("data")
        if not isinstance(dd, dict) or dd.get("subtype") != "交通":
            continue
        items = dd.get("items")
        if not isinstance(items, list):
            continue
        for ii, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            slot_label = title or "33座旅游巴士"
            print(f"transport[{ci}] item[{ii}] {title!r} → flyai keyword-search…", flush=True)
            urls = flyai_transport_mainpics(
                item_title=title,
                slot_label=slot_label,
                data=data,
                timeout=flyai_timeout,
                min_images=max(min_images, max_images),
            )
            urls = urls[:max_images]
            if len(urls) < min_images:
                print(
                    f"WARN 仅获得 {len(urls)} 张 https 车图（目标 ≥{min_images}）",
                    file=sys.stderr,
                )
            if not urls:
                print("WARN 飞猪未返回可用 picUrl，跳过", file=sys.stderr)
                continue
            packed = _pack_transport_images(urls, slot_label)
            print(f"  -> {len(urls)} url(s), {len(packed)} slot(s)", flush=True)
            if not dry_run:
                item["images"] = packed
            updated += 1
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description="飞猪网络 keyword-search → 交通用车配图")
    ap.add_argument("trip_json", help="tripData.json 路径")
    ap.add_argument("--min-images", type=int, default=4, help="每条目至少几张（默认 4）")
    ap.add_argument("--max-images", type=int, default=4, help="最多采用几张（默认 4）")
    ap.add_argument("--flyai-timeout", type=int, default=55, help="flyai 子进程超时秒")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    trip_path = Path(args.trip_json).resolve()
    if not trip_path.is_file():
        print(f"文件不存在: {trip_path}", file=sys.stderr)
        return 1

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    n = refill_transport_images(
        data,
        min_images=args.min_images,
        max_images=args.max_images,
        flyai_timeout=args.flyai_timeout,
        dry_run=args.dry_run,
    )
    if n == 0:
        print("未找到 subtype=交通 的 feature 条目", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"dry-run: 将更新 {n} 个交通条目", flush=True)
        return 0

    meta = data.setdefault("meta", {})
    meta["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    trip_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {trip_path}（{n} 个交通条目）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
