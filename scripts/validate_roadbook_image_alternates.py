#!/usr/bin/env python3
"""校验 roadbook v2 tripData：每个带 slotLabel 的图片槽累计 URL 数是否达到下限。

默认 ``--min`` 与交付链路一致（当前默认 **4**，见 ``roadbook_image_alternate_defaults``）。``url`` 与 ``alternates`` 中的字符串均计入。
位于 ``text-block`` 且 ``subtype`` 为 **费用 / 服务 / 须知** 的配图槽 **不参与校验**（该类章节无章节背景配图）。
``cover.logo`` 封面固定品牌 Logo **不参与校验**（逻辑路径 ``roadbook-images/logo-brand-wdtrip.png``）。

用法:
  python3 scripts/validate_roadbook_image_alternates.py "某路书/tripData.json"
  python3 scripts/validate_roadbook_image_alternates.py "某路书/tripData.json" --min 4 --require-remote-urls
  python3 scripts/validate_roadbook_image_alternates.py "某路书/tripData.json" --min 4 --require-remote-urls --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 与 fill_xhs_images 同源逻辑，避免漂移
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from brand_logo import is_cover_brand_logo_slot
from fill_xhs_images import (  # noqa: E402
    collect_image_entries,
    existing_urls,
    is_fee_or_service_text_block_image_slot,
)
from roadbook_image_alternate_defaults import resolved_alternate_bounds  # noqa: E402
from xhs_image_url_rules import is_remote_https_image_url  # noqa: E402


def main() -> int:
    alt_min, alt_max = resolved_alternate_bounds()
    ap = argparse.ArgumentParser(description="Validate min URL count per image slot in tripData.")
    ap.add_argument("trip_json", help="Path to tripData.json")
    ap.add_argument(
        "--min",
        type=int,
        default=alt_min,
        help="Minimum unique URLs per slot（默认与 fill_xhs/deliver 一致，可由 ROADBOOK_V2_IMAGE_ALTERNATES* 覆盖）",
    )
    ap.add_argument(
        "--require-remote-urls",
        action="store_true",
        help="每张 URL 须为 https 远端（禁止 roadbook-images/ 等本地相对路径）；与小红书交付管线对齐",
    )
    ap.add_argument("--json", action="store_true", help="Print machine-readable report to stdout")
    args = ap.parse_args()

    path = Path(args.trip_json).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if args.min < 1:
        raise SystemExit("--min must be >= 1")

    entries = list(collect_image_entries(data))
    checked = [
        (sp, en)
        for sp, en in entries
        if not is_fee_or_service_text_block_image_slot(data, sp) and not is_cover_brand_logo_slot(sp)
    ]
    failures: list[dict] = []
    for seg_path, entry in checked:
        urls = existing_urls(entry)
        n = len(urls)
        bad_local = [u for u in urls if not is_remote_https_image_url(u)]
        if n < args.min:
            failures.append(
                {
                    "path": "/".join(str(p) for p in seg_path),
                    "slotLabel": entry.get("slotLabel", ""),
                    "count": n,
                    "required": args.min,
                    "reason": "count",
                }
            )
        elif args.require_remote_urls and bad_local:
            failures.append(
                {
                    "path": "/".join(str(p) for p in seg_path),
                    "slotLabel": entry.get("slotLabel", ""),
                    "count": n,
                    "required": args.min,
                    "reason": "non_remote",
                    "samples": bad_local[:3],
                }
            )

    if args.json:
        print(json.dumps({"ok": not failures, "failures": failures}, ensure_ascii=False, indent=2))
    elif failures:
        print(f"未达标: {len(failures)} 个槽位", file=sys.stderr)
        for f in failures:
            extra = ""
            if f.get("reason") == "non_remote":
                extra = f" non_https/local={f.get('samples')!r}"
            print(
                f"  - {f['path']!r} label={f['slotLabel']!r} count={f['count']} required={f['required']}{extra}",
                file=sys.stderr,
            )
        print("\n请先运行:", file=sys.stderr)
        print(
            f'  python3 scripts/fill_xhs_images.py "{path}" '
            f"--min-images {args.min} --max-images {alt_max} --require-remote-urls",
            file=sys.stderr,
        )
        return 1

    if not args.json:
        msg = f"OK: 全部图片槽 URL 数 >= {args.min}（共 {len(checked)} 个槽；费用/服务正文页与封面品牌 Logo 不参与）"
        if args.require_remote_urls:
            msg += "，且均为 https 远端 URL"
        print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
