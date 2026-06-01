#!/usr/bin/env python3
"""将简表中的费用类 / 服务类段落合并进已有 roadbook-v2 ``tripData.json``。

解析规则与 ``roadbook_intake.extract_sections_from_text`` / ``parse_brief`` 一致：
需在「费用明细」或「报价」标题之后出现 ``用车：…`` 等键值行；服务类「标题：正文」也可出现在全文。

用法（一般由 ``deliver_roadbook_v2.py --intake-brief`` 调用）::

    python3 scripts/merge_intake_fee_service.py 某路书/tripData.json 某路书/sources/brief.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from roadbook_intake import (  # noqa: E402
    SERVICE_SECTION_HEADINGS_ORDER,
    _cost_sections_to_lines,
    _extract_price_summary,
    _format_section_body,
    _lines_to_ul_html,
    extract_sections_from_text,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge fee/service text-blocks from brief into tripData.")
    ap.add_argument("trip_json", help="Path to tripData.json")
    ap.add_argument("brief_path", help="Brief .txt or .md")
    args = ap.parse_args()

    trip_path = Path(args.trip_json).resolve()
    brief_path = Path(args.brief_path).resolve()
    raw = brief_path.read_text(encoding="utf-8")
    sections = extract_sections_from_text(raw)
    prices = _extract_price_summary(raw)

    cost_sections: list[dict] = []
    for key in ("用车", "住宿", "门票", "保险", "餐食", "导服", "其他", "不含", "费用不含"):
        if sections.get(key):
            cost_sections.append({"heading": key, "body": _format_section_body(key, sections[key])})

    price_line: list[str] = []
    if "adult" in prices:
        price_line.append(f"成人：¥{prices['adult']}/人")
    if "child" in prices:
        price_line.append(f"儿童：¥{prices['child']}/人")
    if price_line:
        cost_sections.insert(
            0,
            {"heading": "行程报价", "body": _format_section_body("行程报价", "；".join(price_line))},
        )

    service_sections: list[dict] = []
    for heading in SERVICE_SECTION_HEADINGS_ORDER:
        if sections.get(heading):
            service_sections.append({"heading": heading, "body": _format_section_body(heading, sections[heading])})

    if not cost_sections and not service_sections:
        print(
            "merge_intake_fee_service: 简表未解析到费用/服务段落（需「费用明细」或「报价」区块及服务类标题行），跳过",
            flush=True,
        )
        return 0

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    comps = data.get("components")
    if not isinstance(comps, list):
        raise SystemExit("tripData.components 无效")

    touched = 0
    created_service = False

    has_service_comp = any(isinstance(c, dict) and c.get("id") == "text-service-001" for c in comps)
    if service_sections and not has_service_comp:
        ul = _lines_to_ul_html(_cost_sections_to_lines(service_sections))
        comps.append(
            {
                "type": "text-block",
                "id": "text-service-001",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": "服务说明"},
                    "title": "服务说明",
                    "subtype": "服务",
                    "content": ul,
                },
            }
        )
        touched += 1
        created_service = True

    for comp in comps:
        if not isinstance(comp, dict):
            continue
        cid = comp.get("id")
        dd = comp.get("data")
        if not isinstance(dd, dict):
            continue

        if cid == "text-cost-001" and cost_sections:
            dd["content"] = _lines_to_ul_html(_cost_sections_to_lines(cost_sections))
            if "sections" in dd:
                del dd["sections"]
            touched += 1

        if cid == "text-service-001" and service_sections and not created_service:
            ul = _lines_to_ul_html(_cost_sections_to_lines(service_sections))
            dd["content"] = ul
            if "sections" in dd:
                del dd["sections"]
            touched += 1

    if not touched:
        print(
            "merge_intake_fee_service: tripData 中缺少 text-cost-001 / text-service-001，或未匹配到可写入段落",
            file=sys.stderr,
            flush=True,
        )
        return 1

    meta = data.setdefault("meta", {})
    meta["updatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    trip_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merge_intake_fee_service: 已合并费用/服务正文 → {trip_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
