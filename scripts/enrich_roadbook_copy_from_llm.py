#!/usr/bin/env python3
"""按 SKILL 规范用 LLM 润色 tripData：行程亮点、费用说明、服务说明。

不修改图片 URL；失败时保留原稿并打印 WARN，退出码 0（不阻断 deliver）。

用法:
  python3 scripts/enrich_roadbook_copy_from_llm.py "路书目录/tripData.json" --force
  python3 scripts/enrich_roadbook_copy_from_llm.py trip.json --brief sources/itinerary-brief.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from llm_openai_compat import (  # noqa: E402
    chat_completions_json,
    default_chat_model_id,
    resolve_llm_http_config,
)
from repo_dotenv import load_repo_dotenv  # noqa: E402

try:
    from tripdata_content_limits import warn_oversized_fields  # noqa: E402
except ImportError:

    def warn_oversized_fields(_data: dict, *, label: str = "tripData") -> int:  # type: ignore
        return 0


COPY_LLM_SYSTEM = """你是「玩点旅行」资深定制路书编辑，负责润色客户路书中的三块文案（非每日行程正文）。

【行程亮点 highlights】
- 输出一段总述 content（约 80–150 字，中文）+ items 数组 3–5 条，每条 1–2 句话。
- 维度须覆盖：游玩体验、住宿特色、美食亮点；可按素材增设文化体验、行程留白等 1–2 项。
- 突出辨识度与度假感，禁止空洞口号、小红书腔、emoji、导流。
- 不得编造未在素材出现的景点、价格、「全含/赠送」等承诺。

【费用说明 fee】与【服务说明 service】
- 仅输出 HTML 字符串 content，结构固定：
  一两段导语 <p>…</p> + 一个 <ul class="textblock-lines textblock-rich-bullets">，每条一项 <li>…</li>。
- 费用块：仅写报价、包含/不含、用车/门票/保险/餐食等计价与打包范围；勿写服务承诺、退改细则。
- 服务块：仅写服务范围、预订签约、出行须知、注意事项、退改政策、服务承诺；勿把纯计价条款写入服务块。
- 勿使用 sections 多分栏；勿写对内编辑/流水线说明。

【输出】
- 只返回一个 JSON 对象，不要 Markdown 围栏外的任何文字。
- 键名固定：highlights（含 content、items）、fee（含 content）、service（含 content）。
- 某块素材不足无法合规撰写时，该键可省略，不要编造。"""

INTAKE_BOILERPLATE_MARKERS = (
    "路书数据结构支持在线编辑",
    "支持按客户画像",
    "二次销售复用",
)

_TAG_RE = re.compile(r"<[^>]+>")


def _plain_len(html: str) -> int:
    return len(_TAG_RE.sub(" ", html or "").split())


def _find_component(comps: list, *, ctype: str | None = None, cid: str | None = None) -> dict | None:
    for c in comps:
        if not isinstance(c, dict):
            continue
        if cid and c.get("id") == cid:
            return c
        if ctype and c.get("type") == ctype and not cid:
            return c
    return None


def _collect_trip_context(data: dict) -> dict[str, Any]:
    meta = data.get("meta") or {}
    title = str(meta.get("title") or "").strip()
    days: list[str] = []
    for c in data.get("components") or []:
        if not isinstance(c, dict) or c.get("type") != "daily":
            continue
        d = c.get("data") or {}
        theme = str(d.get("theme") or "").strip()
        date_l = str(d.get("date") or "").strip()
        desc = str(d.get("description") or "").strip()[:280]
        if theme:
            days.append(f"{date_l} {theme}" + (f" | {desc}" if desc else ""))
    comps = data.get("components") or []
    hl = _find_component(comps, ctype="highlights") or {}
    hl_data = hl.get("data") or {}
    cost = _find_component(comps, cid="text-cost-001") or {}
    svc = _find_component(comps, cid="text-service-001") or {}
    return {
        "title": title,
        "days": days,
        "highlights_items": hl_data.get("items") or [],
        "highlights_content": str(hl_data.get("content") or "").strip(),
        "fee_content": str((cost.get("data") or {}).get("content") or "").strip(),
        "service_content": str((svc.get("data") or {}).get("content") or "").strip(),
    }


def _highlights_needs_rewrite(data: dict, *, force: bool) -> bool:
    if force:
        return True
    hl = _find_component(data.get("components") or [], ctype="highlights")
    if not hl:
        return False
    d = hl.get("data") or {}
    items = d.get("items") or []
    if not isinstance(items, list) or len(items) < 3:
        return True
    for it in items:
        s = str(it)
        if any(m in s for m in INTAKE_BOILERPLATE_MARKERS):
            return True
    if not str(d.get("content") or "").strip():
        return True
    return False


def _textblock_needs_rewrite(content: str, *, force: bool, min_plain: int = 80) -> bool:
    if force:
        return True
    if not content.strip():
        return True
    if _plain_len(content) < min_plain:
        return True
    if "textblock-rich-bullets" not in content and "<li>" not in content.lower():
        return True
    return False


def _validate_highlights(block: Any) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    content = str(block.get("content") or "").strip()
    items = block.get("items")
    if not isinstance(items, list):
        return None
    clean_items = [str(x).strip() for x in items if str(x).strip()]
    if len(clean_items) < 3 or len(clean_items) > 6:
        print(f"WARN highlights.items 条数 {len(clean_items)} 不在 3–5 范围，跳过亮点", flush=True)
        return None
    if content and len(content) > 400:
        content = content[:380] + "…"
    return {"content": content, "items": clean_items[:5]}


def _validate_html_block(block: Any, *, label: str) -> str | None:
    if not isinstance(block, dict):
        return None
    content = str(block.get("content") or "").strip()
    if not content:
        return None
    if "<li>" not in content.lower():
        print(f"WARN {label}.content 缺少 <li>，跳过", flush=True)
        return None
    if "textblock-rich-bullets" not in content:
        if "<ul" in content.lower():
            content = content.replace("<ul", '<ul class="textblock-lines textblock-rich-bullets"', 1)
        else:
            print(f"WARN {label}.content 缺少推荐 ul 类名，跳过", flush=True)
            return None
    return content


def _apply_patch(data: dict, patch: dict[str, Any]) -> int:
    comps = data.get("components")
    if not isinstance(comps, list):
        return 0
    n = 0

    hl_patch = patch.get("highlights")
    if hl_patch is not None:
        validated = _validate_highlights(hl_patch)
        if validated:
            hl = _find_component(comps, ctype="highlights")
            if hl:
                d = hl.setdefault("data", {})
                if validated.get("content"):
                    d["content"] = validated["content"]
                d["items"] = validated["items"]
                n += 1
                print(f"INFO 已更新行程亮点（{len(validated['items'])} 条）", flush=True)

    fee_patch = patch.get("fee")
    if fee_patch is not None:
        html = _validate_html_block(fee_patch, label="fee")
        if html:
            cost = _find_component(comps, cid="text-cost-001")
            if cost:
                cost.setdefault("data", {})["content"] = html
                n += 1
                print("INFO 已更新费用说明", flush=True)

    svc_patch = patch.get("service")
    if svc_patch is not None:
        html = _validate_html_block(svc_patch, label="service")
        if html:
            svc = _find_component(comps, cid="text-service-001")
            if svc:
                svc.setdefault("data", {})["content"] = html
                n += 1
                print("INFO 已更新服务说明", flush=True)

    return n


def enrich_copy_from_llm(
    data: dict,
    *,
    brief_text: str,
    force: bool,
    use_llm: bool,
    model: str,
    timeout_s: int,
) -> int:
    ctx = _collect_trip_context(data)
    need_hl = _highlights_needs_rewrite(data, force=force)
    cost_c = ctx["fee_content"]
    svc_c = ctx["service_content"]
    need_fee = _textblock_needs_rewrite(cost_c, force=force)
    need_svc = _textblock_needs_rewrite(svc_c, force=force)

    if not (need_hl or need_fee or need_svc):
        print("INFO 行程亮点/费用/服务已达交付口径，跳过 copy LLM", flush=True)
        return 0

    if not use_llm:
        print("INFO --no-llm：跳过 enrich_roadbook_copy_from_llm", flush=True)
        return 0

    api_key, _ = resolve_llm_http_config()
    if not api_key:
        print("INFO 未设置 OPENAI_API_KEY / DEEPSEEK_API_KEY，跳过 copy LLM", flush=True)
        return 0

    brief_trim = (brief_text or "").strip()[:12000]
    user_parts = [
        f"路书标题：{ctx['title'] or '（未提供）'}",
        "",
        "【每日概要】",
    ]
    if ctx["days"]:
        user_parts.extend(f"- {line}" for line in ctx["days"][:12])
    else:
        user_parts.append("（无 daily 组件）")
    user_parts.append("")
    if brief_trim:
        user_parts.extend(["【客户简表/报价原文】", brief_trim, ""])
    if need_hl:
        user_parts.extend(
            [
                "【当前行程亮点草稿】",
                f"content: {ctx['highlights_content'] or '（空）'}",
                f"items: {json.dumps(ctx['highlights_items'], ensure_ascii=False)}",
                "",
            ]
        )
    if need_fee and cost_c:
        user_parts.extend(["【当前费用说明 HTML】", cost_c[:6000], ""])
    if need_svc and svc_c:
        user_parts.extend(["【当前服务说明 HTML】", svc_c[:6000], ""])

    tasks = []
    if need_hl:
        tasks.append("highlights")
    if need_fee:
        tasks.append("fee")
    if need_svc:
        tasks.append("service")
    user_parts.append(f"请润色并返回 JSON 键：{', '.join(tasks)}。")

    patch = chat_completions_json(
        system=COPY_LLM_SYSTEM,
        user="\n".join(user_parts),
        model=model,
        timeout_s=timeout_s,
        max_tokens=2800,
        temperature=0.48,
    )
    if not patch:
        print("WARN enrich_roadbook_copy_from_llm：LLM 无有效 JSON，保留原稿", flush=True)
        return 0

    return _apply_patch(data, patch)


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 润色 tripData 亮点/费用/服务（SKILL 口径）")
    ap.add_argument("trip_json", help="tripData.json 路径")
    ap.add_argument("--brief", default="", help="可选：行程简表 txt/md，供 LLM 核对报价与包含项")
    ap.add_argument(
        "--force",
        action="store_true",
        help="强制重写亮点/费用/服务（deliver 默认传入）",
    )
    ap.add_argument("--no-llm", action="store_true", help="禁用 LLM，直接退出")
    ap.add_argument("--llm-model", default="", help="覆盖 OPENAI_MODEL / DEEPSEEK_MODEL")
    ap.add_argument("--timeout-s", type=int, default=120, help="LLM 请求超时（秒）")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    load_repo_dotenv(root)

    path = Path(args.trip_json).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))

    brief_text = ""
    if args.brief:
        brief_text = Path(args.brief).read_text(encoding="utf-8", errors="replace")

    model = (args.llm_model or default_chat_model_id()).strip()
    n = enrich_copy_from_llm(
        data,
        brief_text=brief_text,
        force=args.force,
        use_llm=not args.no_llm,
        model=model,
        timeout_s=max(30, args.timeout_s),
    )

    meta = data.setdefault("meta", {})
    meta["updatedAt"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    warn_oversized_fields(data, label=path.name)
    print(f"OK enrich_roadbook_copy_from_llm：更新 {n} 块 → {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
