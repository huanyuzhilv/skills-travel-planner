#!/usr/bin/env python3
"""
当 tripData 里图片槽位无 url（仅有 slotLabel / 空 alternates），但目录下已有 roadbook-images/*.jpg 时，
按遍历顺序为每个槽位写入相对路径 url（循环使用图片列表），便于离线预览恢复画面。

用法:
  python3 scripts/relink_local_roadbook_images.py "generated-roadbooks/贵州黔南环线-2026-05/tripData.json"
  python3 scripts/relink_local_roadbook_images.py "generated-roadbooks/贵州黔南环线-2026-05/tripData.json" --dry-run
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any, Iterator


def _needs_url(obj: dict) -> bool:
    if not isinstance(obj, dict) or "alternates" not in obj:
        return False
    u = str(obj.get("url") or obj.get("src") or "").strip()
    if u:
        return False
    for a in obj.get("alternates") or []:
        if isinstance(a, str) and a.strip():
            return False
        if isinstance(a, dict) and str(a.get("url") or a.get("src") or "").strip():
            return False
    return True


def _assign_walk(node: Any, rel_urls: Iterator[str]) -> int:
    n = 0
    if isinstance(node, dict):
        if _needs_url(node):
            node["url"] = next(rel_urls)
            n += 1
        for v in node.values():
            n += _assign_walk(v, rel_urls)
    elif isinstance(node, list):
        for item in node:
            n += _assign_walk(item, rel_urls)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trip_json", type=str, help="tripData.json 路径")
    ap.add_argument("--assets-dir", type=str, default="roadbook-images", help="与 tripData 同级的图片目录名")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.trip_json).resolve()
    base = path.parent
    img_dir = base / args.assets_dir
    jpgs = sorted(img_dir.glob("*.jpg")) + sorted(img_dir.glob("*.jpeg")) + sorted(img_dir.glob("*.png"))
    if not jpgs:
        raise SystemExit(f"未找到图片: {img_dir}")

    rel = [f"{args.assets_dir}/{p.name}" for p in jpgs]
    cycle = itertools.cycle(rel)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    filled = _assign_walk(data, cycle)
    print(f"槽位写入 url: {filled}，使用本地图 {len(jpgs)} 张（不足则循环）")

    if args.dry_run:
        return

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存: {path}")


if __name__ == "__main__":
    main()
