#!/usr/bin/env python3
"""Build roadbook-v2 tripData from lightweight itinerary briefs.

Supports plain-text itinerary and cost notes such as:
- D1 到达贵阳
- D2 贵阳-黄果树大瀑布-罗甸
- ...

Output:
- <output-dir>/tripData.json
- optionally render HTML/PDF via assets/generate.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from brand_logo import brand_logo_relative_url
from roadbook_constants import DEFAULT_DAILY_DESCRIPTION_BOILERPLATE

DAY_LINE_RE = re.compile(r"^\s*[Dd]\s*(\d+)\s*(?:[：:\-–—]?\s*)?(.+?)\s*$")
# Excel/表格粘贴：5月16日	D1	从江-堂安梯田… 或规范化后「5月16日 D1 从江…」
DAY_EMBEDDED_RE = re.compile(
    r"^(?:\d{1,2}月\d{1,2}日\s+)?[Dd](\d+)\s+(.+)$"
)

# 兼容多种「天数」写法：D1 / d1 / Day1 / Day 1 / DAY1 / 第1天 / 第一天 / 第1日
# （允许行首的 - • * # 等列表/标题符号；千问回退或用户直接粘贴自然语言时不再硬崩）
_DAY_LATIN_RE = re.compile(
    r"^[\s\-•*#]*[Dd](?:ay|AY|ai)?\s*0*(\d{1,3})\s*(?:[：:、.\-–—]\s*)?(.*)$"
)
_DAY_CN_RE = re.compile(
    r"^[\s\-•*#]*第\s*([0-9一二两三四五六七八九十]{1,3})\s*[天日]\s*(?:[：:、.\-–—]\s*)?(.*)$"
)
_CN_NUMERALS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_numeral_to_int(token: str) -> int | None:
    """简体中文小数（一~九十九）转 int；非中文数字返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_NUMERALS:
        return _CN_NUMERALS[token]
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_NUMERALS.get(head, 1) if head else 1
        ones = _CN_NUMERALS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return None


def _match_day_marker(norm: str) -> Tuple[int, str] | None:
    """识别一行是否为「某天」标题，返回 (天序号, 当天行内剩余文本)。"""
    m = _DAY_CN_RE.match(norm)
    if m:
        day_no = _cn_numeral_to_int(m.group(1))
        if day_no and day_no > 0:
            return day_no, m.group(2).strip()
    m = _DAY_LATIN_RE.match(norm)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None


def _is_fee_or_section_start(stripped: str, norm: str) -> bool:
    """判断一行是否为费用/报价/服务块起始，避免把它误并入上一天的描述。"""
    if stripped.startswith("费用明细") or stripped.startswith("报价"):
        return True
    if QUOTE_HEADER_START_RE.match(stripped):
        return True
    m = CITY_LINE_RE.match(norm)
    if m:
        key = m.group(1).strip()
        if key in SECTION_KEYS or key in SERVICE_MERGE_KEYS:
            return True
    return False


_PROSE_THEME_MAX = 18


def _split_prose_theme(raw: str) -> Tuple[str, str]:
    """无路线连字符的自然语言行：取首个短句作主题，其余并入当天描述。

    返回 (theme, inline_remainder)。含 ``-/→`` 的路线行原样返回。
    """
    text = (raw or "").strip()
    if not text:
        return text, ""
    if any(sep in text for sep in ("-", "→", "—", "－")):
        return text, ""
    if len(text) <= _PROSE_THEME_MAX:
        return text, ""
    m = re.search(r"[，,。；;！!]", text)
    if not m or m.start() == 0 or m.start() > _PROSE_THEME_MAX:
        head = text[:_PROSE_THEME_MAX].strip(" ，,。；;")
        tail = text[len(head):].strip(" ，,。；;")
        return head, tail
    head = text[: m.start()].strip(" ，,。；;")
    tail = text[m.start() + 1 :].strip(" ，,。；;")
    return head, tail


def _extract_doc_title(raw_lines: List[str]) -> str:
    """首个天数标题之前若有标题行（如「济南3日｜泉城慢旅」），作为路书展示标题。"""
    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        norm = re.sub(r"\s+", " ", s).strip()
        if "\t" in s and _try_parse_table_day_line(s):
            return ""
        if _match_day_marker(norm) or DAY_EMBEDDED_RE.match(norm):
            return ""
        if any(m in s for m in TABLE_HEADER_MARKERS):
            return ""
        if _is_fee_or_section_start(s, norm):
            return ""
        return s
    return ""
TABLE_HEADER_MARKERS = ("行程内容", "天数", "时间", "行程区间")
CITY_LINE_RE = re.compile(r"^\s*([^\s：:]{1,12})\s*[：:]\s*(.+?)\s*$")
QUOTE_BLOCK_NAMES = ("报价包含", "报价不包含")
QUOTE_HEADER_START_RE = re.compile(r"^报价(包含|不包含)(?:\s|$)")
NUMBERED_QUOTE_ITEM_RE = re.compile(
    r"(?:报价(?:包含|不包含)\s*)?"
    r"(\d+)\.\s*"
    r"([^：:]{1,24}?)\s*[：:]\s*"
    r"(.+)$"
)
# 报价包含条目 → sections 键（供住宿/交通 feature 使用，费用正文仍以「报价包含」块为准）
INCLUDED_ITEM_TO_SECTION = {
    "酒店": "住宿",
    "用车": "用车",
    "首道门票": "门票",
    "门票": "门票",
    "保险": "保险",
    "小费": "其他",
    "其他": "其他",
}
HOTEL_CELL_HINTS = ("酒店", "客栈", "民宿", "或同级")

SECTION_KEYS = {
    "用车",
    "住宿",
    "门票",
    "保险",
    "餐食",
    "导服",
    "其他",
    "不含",
    "费用不含",
    "行程共计",
    "总价",
    # 服务说明（与「费用明细」同一大段中出现时写入 text-block 服务，不进费用列表）
    "服务说明",
    "出行须知",
    "预订须知",
    "退改政策",
    "退改说明",
    "注意事项",
    "温馨提示",
    "服务范围",
    "服务承诺",
}

# 从报价/简表中抽取后写入「服务说明」组件（顺序稳定）
SERVICE_SECTION_HEADINGS_ORDER = (
    "服务说明",
    "服务范围",
    "服务承诺",
    "预订须知",
    "出行须知",
    "注意事项",
    "温馨提示",
    "退改政策",
    "退改说明",
)


SERVICE_MERGE_KEYS = frozenset(SERVICE_SECTION_HEADINGS_ORDER)


def _derive_textblock_lines_from_legacy_sections(sections: List) -> List[str]:
    """与 v2 模板中 deriveTextblockLinesFromLegacySections 一致：旧 sections[] → 列表行。"""
    out: List[str] = []
    if not isinstance(sections, list):
        return out
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        h = str(sec.get("heading") or "").strip()
        body = str(sec.get("body") or "").strip()
        if not body and not h:
            continue
        parts = [p.strip() for p in re.split(r"[；;\n\r]+", body) if p.strip()]
        if not parts:
            if h:
                out.append(h)
            continue
        for i, p in enumerate(parts):
            if i == 0 and h:
                out.append(f"{h}：{p}")
            else:
                out.append(p)
    return out


def _lines_to_textblock_rich_ul(lines: List[str]) -> str:
    if not lines:
        return ""
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f'<ul class="textblock-lines textblock-rich-bullets">{items}</ul>'


def _notice_block_supplementary_html(data: Dict) -> str:
    """从 subtype「须知」块提取可并入「服务说明」的 HTML 片段（content + sections 列表）。"""
    frags: List[str] = []
    raw_content = data.get("content")
    if isinstance(raw_content, str) and raw_content.strip():
        frags.append(raw_content.strip())
    lines = _derive_textblock_lines_from_legacy_sections(
        data.get("sections") if isinstance(data.get("sections"), list) else []
    )
    ul = _lines_to_textblock_rich_ul(lines)
    if ul:
        frags.append(ul)
    return "".join(frags)


def strip_empty_travel_notice_text_blocks(data: Dict) -> bool:
    """删除无正文的「须知」组件：输入未提供出行须知时不占版、不生成空页。"""
    meta = data.get("meta") or {}
    if meta.get("version") != "2.0":
        return False
    comps = data.get("components")
    if not isinstance(comps, list):
        return False
    to_pop: List[int] = []
    for i, c in enumerate(comps):
        if not isinstance(c, dict) or c.get("type") != "text-block":
            continue
        dd = c.get("data")
        if not isinstance(dd, dict) or dd.get("subtype") != "须知":
            continue
        if not _notice_block_supplementary_html(dd):
            to_pop.append(i)
    for i in reversed(to_pop):
        comps.pop(i)
    return bool(to_pop)


def normalize_lone_travel_notice_as_service(data: Dict) -> bool:
    """若无「服务」、仅有带正文的「须知」，则合并为一页「服务说明」。

    多个须知块合并进同一服务块；无正文的须知已由 strip_empty_travel_notice_text_blocks 删除，此处不用默认文案填充。
    """
    meta = data.get("meta") or {}
    if meta.get("version") != "2.0":
        return False
    comps = data.get("components")
    if not isinstance(comps, list):
        return False

    has_service = any(
        isinstance(c, dict)
        and c.get("type") == "text-block"
        and isinstance(c.get("data"), dict)
        and c["data"].get("subtype") == "服务"
        for c in comps
    )
    if has_service:
        return False

    pairs: List[Tuple[int, str]] = []
    for i, c in enumerate(comps):
        if not isinstance(c, dict) or c.get("type") != "text-block":
            continue
        dd = c.get("data")
        if not isinstance(dd, dict) or dd.get("subtype") != "须知":
            continue
        extra = _notice_block_supplementary_html(dd)
        if extra:
            pairs.append((i, extra))
    if not pairs:
        return False

    pairs.sort(key=lambda x: x[0])
    combined = "".join(p[1] for p in pairs)
    first_i = pairs[0][0]
    dd = comps[first_i]["data"]
    if not isinstance(dd, dict):
        return False
    dd["subtype"] = "服务"
    dd["title"] = "服务说明"
    dd["content"] = combined
    for k in ("sections", "lines"):
        if k in dd:
            del dd[k]
    if isinstance(dd.get("backgroundImage"), dict):
        dd["backgroundImage"]["slotLabel"] = "服务说明"
    for i, _ in reversed(pairs[1:]):
        comps.pop(i)
    return True


def merge_travel_notice_into_service_text_block(data: Dict) -> bool:
    """若同时存在「服务」与「须知」，将须知正文依次并入服务说明并删除须知块。"""
    meta = data.get("meta") or {}
    if meta.get("version") != "2.0":
        return False
    comps = data.get("components")
    if not isinstance(comps, list):
        return False

    changed = False
    while True:
        idx_svc = idx_ntc = -1
        for i, c in enumerate(comps):
            if not isinstance(c, dict) or c.get("type") != "text-block":
                continue
            dd = c.get("data")
            if not isinstance(dd, dict):
                continue
            st = dd.get("subtype")
            if st == "服务" and idx_svc < 0:
                idx_svc = i
            elif st == "须知" and idx_ntc < 0:
                idx_ntc = i

        if idx_svc < 0 or idx_ntc < 0:
            break

        svc_block = comps[idx_svc]
        ntc_block = comps[idx_ntc]
        sd = svc_block.setdefault("data", {})
        nd = ntc_block.get("data") if isinstance(ntc_block.get("data"), dict) else {}

        extra = _notice_block_supplementary_html(nd)
        if extra:
            base = str(sd.get("content") or "").strip()
            sd["content"] = f"{base}{extra}" if base else extra
        sd["title"] = "服务说明"
        comps.pop(idx_ntc)
        changed = True
    return changed


def align_travel_notice_with_service_text_block(data: Dict) -> bool:
    """出行须知随输入：无正文则移除；有正文则并入服务说明页（版式与费用/服务一致）。"""
    a = strip_empty_travel_notice_text_blocks(data)
    b = merge_travel_notice_into_service_text_block(data)
    c = normalize_lone_travel_notice_as_service(data)
    return a or b or c


def ensure_default_service_text_block(data: Dict) -> bool:
    """roadbook v2：若已有「费用说明」但缺少「服务」text-block，则在费用块后插入**空白**服务说明页。

    无简表/无服务段落时不写任何默认长文，便于顾问在编辑器或 JSON 中手填。
    """
    meta = data.get("meta") or {}
    if meta.get("version") != "2.0":
        return False
    comps = data.get("components")
    if not isinstance(comps, list):
        return False

    has_service = False
    fee_idx = -1
    for i, c in enumerate(comps):
        if not isinstance(c, dict) or c.get("type") != "text-block":
            continue
        dd = c.get("data")
        if not isinstance(dd, dict):
            continue
        st = dd.get("subtype")
        if st == "服务":
            has_service = True
        if st == "费用" or c.get("id") == "text-cost-001":
            fee_idx = i

    if has_service or fee_idx < 0:
        return False

    block = {
        "type": "text-block",
        "id": "text-service-001",
        "data": {
            "backgroundImage": {"alternates": [], "slotLabel": "服务说明"},
            "title": "服务说明",
            "subtype": "服务",
            "content": "",
        },
    }
    comps.insert(fee_idx + 1, block)
    return True


EMPTY_HOTEL_FEATURE_ITEM: Dict[str, Any] = {
    "title": "",
    "description": "",
    "images": [],
}


def _index_after_last_daily(comps: List[Any]) -> int:
    last = -1
    for i, c in enumerate(comps):
        if isinstance(c, dict) and c.get("type") == "daily":
            last = i
    return last + 1 if last >= 0 else len(comps)


def ensure_hotel_feature_module(data: Dict) -> bool:
    """roadbook v2：保证存在「住宿安排」feature；无酒店信息时保留空卡片供手填。"""
    meta = data.get("meta") or {}
    if meta.get("version") != "2.0":
        return False
    comps = data.get("components")
    if not isinstance(comps, list):
        data["components"] = comps = []

    for comp in comps:
        if not isinstance(comp, dict) or comp.get("type") != "feature":
            continue
        dd = comp.get("data")
        if not isinstance(dd, dict) or dd.get("subtype") != "住宿":
            continue
        items = dd.get("items")
        if not isinstance(items, list) or len(items) == 0:
            dd["items"] = [dict(EMPTY_HOTEL_FEATURE_ITEM)]
            return True
        return False

    insert_at = _index_after_last_daily(comps)
    comps.insert(
        insert_at,
        {
            "type": "feature",
            "id": "feature-hotel-001",
            "data": {
                "backgroundImage": {"alternates": [], "slotLabel": "酒店 房型 设施"},
                "title": "住宿安排",
                "subtype": "住宿",
                "items": [dict(EMPTY_HOTEL_FEATURE_ITEM)],
            },
        },
    )
    return True


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".docx":
        return _extract_docx_text(path)
    raise ValueError(f"Unsupported input format: {suffix}. Please provide .txt/.md/.docx")


def _extract_docx_text(path: Path) -> str:
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as f:
            xml_bytes = f.read()

    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: List[str] = []
    for p in root.findall(".//w:p", ns):
        texts = [node.text for node in p.findall(".//w:t", ns) if node.text]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _normalize_lines(raw_text: str) -> List[str]:
    lines: List[str] = []
    for line in raw_text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def _trim_itinerary_tail(text: str) -> str:
    """表格行里行程后与用餐/住宿混在同一格时，只保留动线描述。"""
    text = text.strip()
    if not text:
        return text
    parts = re.split(
        r"\s+(?=午餐|晚餐|早餐|住宿|酒店|航班|备注|或同级)",
        text,
        maxsplit=1,
    )
    return (parts[0] if parts else text).strip(" ，,;；")


def _try_parse_table_day_line(line: str) -> Dict | None:
    """解析制表符分隔的行程表行（从 Excel 复制）。"""
    if "\t" not in line:
        return None
    if any(m in line for m in TABLE_HEADER_MARKERS):
        return None
    parts = [p.strip() for p in line.split("\t")]
    if len(parts) < 2:
        return None
    day_num = None
    for p in parts:
        m = re.fullmatch(r"[Dd](\d+)", p.strip())
        if m:
            day_num = int(m.group(1))
            break
    if day_num is None:
        return None
    content_candidates: List[str] = []
    for p in parts:
        if not p or re.fullmatch(r"[Dd]\d+", p, flags=re.IGNORECASE):
            continue
        if re.match(r"^\d{1,2}月\d{1,2}日", p):
            continue
        if p in ("-", "—", ""):
            continue
        content_candidates.append(p)
    raw = ""
    for p in content_candidates:
        if len(p) > len(raw) and (
            "-" in p or "→" in p or "—" in p or len(p) >= 4
        ):
            raw = p
    if not raw and content_candidates:
        raw = content_candidates[0]
    raw = _trim_itinerary_tail(raw)
    if not raw:
        return None

    hotel_cell = ""
    if len(parts) >= 6:
        for idx, p in enumerate(parts):
            if idx < 3:
                continue
            cell = p.strip()
            if not cell or cell in ("-", "—"):
                continue
            if re.fullmatch(r"[Dd]\d+", cell, flags=re.IGNORECASE):
                continue
            if re.match(r"^\d{1,2}月\d{1,2}日", cell):
                continue
            if "-" in cell or "→" in cell or "—" in cell:
                continue
            if any(h in cell for h in HOTEL_CELL_HINTS):
                hotel_cell = cell
                break

    out: Dict = {"day": day_num, "raw": raw}
    if hotel_cell:
        out["hotel_cell"] = hotel_cell
    return out


def _collect_day_items(raw_lines: List[str]) -> List[Dict]:
    """从简表收集 D1/D2…（兼容 Day1/第N天/第一天、Excel 表格粘贴）。

    天数标题行之后、下一处天数标题或费用/服务块之前的自由文本，
    作为当天描述写入 ``desc_lines``，便于自然语言行程也能产出可用正文。
    """
    day_items: List[Dict] = []
    current: Dict | None = None
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(m in stripped for m in TABLE_HEADER_MARKERS):
            current = None
            continue

        if "\t" in stripped:
            table_day = _try_parse_table_day_line(stripped)
            if table_day:
                table_day.setdefault("desc_lines", [])
                day_items.append(table_day)
                current = table_day
                continue

        norm = re.sub(r"\s+", " ", stripped).strip()

        marker = _match_day_marker(norm)
        if marker:
            day_no, rest = marker
            item = {"day": day_no, "raw": rest.strip(), "desc_lines": []}
            day_items.append(item)
            current = item
            continue

        emb = DAY_EMBEDDED_RE.match(norm)
        if emb:
            raw = _trim_itinerary_tail(emb.group(2))
            if raw:
                item = {"day": int(emb.group(1)), "raw": raw, "desc_lines": []}
                day_items.append(item)
                current = item
            continue

        # 非天数行：作为上一天的描述续写；遇到费用/报价/服务块则交还给费用解析器
        if current is not None:
            if _is_fee_or_section_start(stripped, norm):
                current = None
            else:
                current.setdefault("desc_lines", []).append(stripped)
    return day_items


def _split_route_nodes(day_text: str) -> List[str]:
    text = day_text.replace("→", "-").replace("—", "-").replace("－", "-")
    nodes = [part.strip(" -") for part in re.split(r"\s*-\s*", text) if part.strip(" -")]
    return nodes or [day_text.strip()]


def _clean_place_token(token: str) -> str:
    token = token.strip()
    token = re.sub(r"^(到达|抵达|离开|返回|返程|入住|前往|赴)", "", token)
    token = re.sub(r"(离开|返程|返回)$", "", token)
    token = token.strip(" ·，,。；;")
    return token or token


def _extract_price_summary(text: str) -> Dict[str, int]:
    prices: Dict[str, int] = {}

    adult = re.search(r"(\d+(?:\.\d+)?)\s*/\s*成人", text)
    child = re.search(r"(\d+(?:\.\d+)?)\s*/\s*儿童", text)

    if not adult:
        adult = re.search(r"成人(?:报价)?\s*[：:]\s*(\d+(?:\.\d+)?)", text)
    if not child:
        child = re.search(r"儿童[^）\)]*[）\)]?\s*(?:报价)?\s*[：:]\s*(\d+(?:\.\d+)?)", text)
    if not adult:
        adult = re.search(r"成人\s*[：:]?\s*(\d+(?:\.\d+)?)", text)
    if not child:
        child = re.search(r"儿童\s*[：:]?\s*(\d+(?:\.\d+)?)", text)

    if adult:
        prices["adult"] = int(float(adult.group(1)))
    if child:
        prices["child"] = int(float(child.group(1)))

    return prices


def _flatten_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _extract_quote_blocks(raw_lines: List[str]) -> Dict[str, str]:
    """解析 Excel 粘贴的「报价包含 / 报价不包含」编号列表（多列 Tab 亦可）。"""
    items_by_block: Dict[str, List[str]] = {"报价包含": [], "报价不包含": []}
    current: str | None = None

    for raw in raw_lines:
        flat = _flatten_line(raw)
        if not flat:
            continue
        hm = QUOTE_HEADER_START_RE.match(flat)
        if hm:
            current = "报价" + hm.group(1)
            tail = flat[hm.end() :].strip()
            if tail:
                im = NUMBERED_QUOTE_ITEM_RE.match(tail)
                if im:
                    items_by_block[current].append(
                        f"{im.group(2).strip()}：{im.group(3).strip()}"
                    )
            continue
        if current:
            im = NUMBERED_QUOTE_ITEM_RE.match(flat)
            if im:
                items_by_block[current].append(
                    f"{im.group(2).strip()}：{im.group(3).strip()}"
                )

    out: Dict[str, str] = {}
    for name, items in items_by_block.items():
        if items:
            out[name] = "\n".join(f"{i + 1}.{text}" for i, text in enumerate(items))
    return out


def _merge_quote_into_sections(sections: Dict[str, str], quotes: Dict[str, str]) -> None:
    for block_name, body in quotes.items():
        sections[block_name] = body
    included = quotes.get("报价包含", "")
    if not included:
        return
    for line in included.splitlines():
        stripped = re.sub(r"^\d+\.\s*", "", line).strip()
        m = re.match(r"([^：:]+)[：:]\s*(.+)", stripped)
        if not m:
            continue
        label, val = m.group(1).strip(), m.group(2).strip()
        sec_key = INCLUDED_ITEM_TO_SECTION.get(label)
        if sec_key and not sections.get(sec_key) and val not in ("无", "-", ""):
            sections[sec_key] = val


def _parse_legacy_cost_lines(lines: List[str]) -> Dict[str, str]:
    """费用明细：键值行（用车：…）；跳过报价包含/不包含（由 _extract_quote_blocks 处理）。"""
    sections: Dict[str, str] = {}
    in_cost_area = False
    current_section = ""

    for line in lines:
        flat = line.strip()
        if flat.startswith("费用明细"):
            in_cost_area = True
            current_section = ""
            continue
        if flat.startswith("报价包含") or flat.startswith("报价不包含"):
            in_cost_area = False
            current_section = ""
            continue
        if flat.startswith("报价"):
            in_cost_area = True
            current_section = ""
            continue

        if not in_cost_area:
            continue

        sec_match = CITY_LINE_RE.match(line)
        if sec_match:
            key = sec_match.group(1).strip()
            val = sec_match.group(2).strip()
            if key in SECTION_KEYS:
                current_section = key
                sections[current_section] = val
                continue

        if current_section:
            sections[current_section] = (sections.get(current_section, "") + "；" + line).strip("；")

    return sections


def parse_fee_sections_from_text(raw_text: str) -> Dict[str, str]:
    """费用/报价段落：兼容「费用明细」键值行与 Excel「报价包含/不包含」编号列表。"""
    lines = _normalize_lines(raw_text)
    raw_lines = [ln for ln in raw_text.splitlines() if ln.strip()]
    sections = _parse_legacy_cost_lines(lines)
    quotes = _extract_quote_blocks(raw_lines)
    _merge_quote_into_sections(sections, quotes)

    for scan in lines:
        m_kv = CITY_LINE_RE.match(scan)
        if not m_kv:
            continue
        sk = m_kv.group(1).strip()
        sv = m_kv.group(2).strip()
        if sk in SERVICE_MERGE_KEYS and not sections.get(sk):
            sections[sk] = sv

    return sections


def _split_hotel_options(raw: str) -> Tuple[List[str], str]:
    level_note = ""
    note_match = re.search(r"(同级[^，。]*)", raw)
    if note_match:
        level_note = note_match.group(1).strip()

    cleaned = raw
    cleaned = re.sub(r"或同级[^，。]*", "", cleaned)
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    options = [item.strip() for item in re.split(r"[、,，]", cleaned) if item.strip()]

    deduped: List[str] = []
    seen = set()
    for opt in options:
        if opt not in seen:
            seen.add(opt)
            deduped.append(opt)
    return deduped, level_note


def _format_hotel_feature_description(
    city: str, hotels: List[str], level_note: str
) -> str:
    """住宿 feature 种子文案：仅保留拟定酒店名，供 enrich 检索；未匹配时由 enrich 清空。"""
    names = "、".join(hotels) if hotels else ""
    if not names:
        return ""
    lines = [f"【拟定酒店】{names}"]
    if level_note:
        lines.append(f"【级别说明】{level_note}")
    if city and city.strip() and city.strip() not in names:
        lines.append(f"（住宿地：{city.strip()}）")
    return "\n".join(lines)


def _normalize_clause_items(text: str, *, split_comma: bool = False) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    primary = [seg.strip(" ，,。;；") for seg in re.split(r"[；;]\s*", raw) if seg.strip(" ，,。;；")]
    if len(primary) > 1:
        return primary
    if "、" in raw:
        return [seg.strip(" ，,。;；") for seg in raw.split("、") if seg.strip(" ，,。;；")]
    if split_comma and ("，" in raw or "," in raw):
        return [seg.strip(" ，,。;；") for seg in re.split(r"[，,]\s*", raw) if seg.strip(" ，,。;；")]
    return [raw]


def _format_section_body(key: str, text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    key = key.strip()
    if key in QUOTE_BLOCK_NAMES:
        return value.strip()

    split_comma = key in {"门票", "不含", "费用不含", "其他", "餐食", "保险", "导服"}
    items = _normalize_clause_items(value, split_comma=split_comma)

    if key == "行程报价":
        rows = [row.strip(" ，,。;；") for row in re.split(r"[；;]\s*", value) if row.strip(" ，,。;；")]
        return "\n".join(rows) if rows else value

    # 住宿通常保留城市分段（已在上游拼接为“城市：xxx；城市：yyy”）
    if key == "住宿":
        parts = [seg.strip() for seg in re.split(r"[；;]\s*", value) if seg.strip()]
        if len(parts) > 1:
            return "\n".join(parts)
        return value

    if len(items) <= 1:
        return value

    return "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(items))


def _cost_sections_to_lines(cost_sections: List[Dict]) -> List[str]:
    """Flatten section dicts into one string per list row (roadbook-v2 费用/服务 text-block)."""
    out: List[str] = []
    for sec in cost_sections:
        heading = str(sec.get("heading") or "").strip()
        body = str(sec.get("body") or "").strip()
        if not body and not heading:
            continue
        parts = [p.strip() for p in re.split(r"[；;\n\r]+", body) if p.strip()]
        if not parts:
            if heading:
                out.append(heading)
            continue
        for i, p in enumerate(parts):
            if i == 0 and heading:
                out.append(f"{heading}：{p}")
            else:
                out.append(p)
    return out


def _lines_to_ul_html(lines: List[str]) -> str:
    """Single rich-text block: optional <ul> for generated bullets (费用/服务同一套 class)."""
    if not lines:
        return ""
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f'<ul class="textblock-lines textblock-rich-bullets">{items}</ul>'


def parse_brief(raw_text: str) -> Dict:
    raw_lines = [ln for ln in raw_text.splitlines() if ln.strip()]
    day_items = _collect_day_items(raw_lines)
    doc_title = _extract_doc_title(raw_lines)
    lines = _normalize_lines(raw_text)

    sections = parse_fee_sections_from_text(raw_text)
    hotel_by_city: Dict[str, List[str]] = {}
    hotel_level_by_city: Dict[str, str] = {}

    # 费用明细里「住宿」下的城市：酒店列表
    in_cost_area = False
    current_section = ""
    for line in lines:
        if line.startswith("费用明细"):
            in_cost_area = True
            current_section = ""
            continue
        if line.startswith("报价包含") or line.startswith("报价不包含"):
            in_cost_area = False
            current_section = ""
            continue
        if not in_cost_area:
            continue
        sec_match = CITY_LINE_RE.match(line)
        if sec_match and current_section == "住宿":
            key = sec_match.group(1).strip()
            val = sec_match.group(2).strip()
            if key not in SECTION_KEYS:
                hotels, level_note = _split_hotel_options(val)
                if hotels:
                    hotel_by_city[key] = hotels
                if level_note:
                    hotel_level_by_city[key] = level_note
            continue
        m_sec = CITY_LINE_RE.match(line)
        if m_sec and m_sec.group(1).strip() in SECTION_KEYS:
            current_section = m_sec.group(1).strip()

    if not day_items:
        raise ValueError(
            "未识别到行程天数。请每行以 D1、D2 开头，或粘贴含「天数」列的表格（如 5月16日\\tD1\\t从江-景点…）。"
        )

    day_items.sort(key=lambda x: x["day"])

    for item in day_items:
        hotel_cell = item.get("hotel_cell") or ""
        if not hotel_cell:
            continue
        nodes = _split_route_nodes(item["raw"])
        cleaned_nodes = [_clean_place_token(n) for n in nodes if _clean_place_token(n)]
        city = cleaned_nodes[-1] if cleaned_nodes else "住宿"
        hotels, level_note = _split_hotel_options(hotel_cell)
        if hotels:
            hotel_by_city[city] = hotels
        if level_note:
            hotel_level_by_city[city] = level_note

    price_text = "\n".join(lines)
    prices = _extract_price_summary(price_text)

    itinerary_rows = []
    daily_components = []
    place_pool: List[str] = []

    for item in day_items:
        day_no = item["day"]
        desc_lines = list(item.get("desc_lines") or [])
        raw = item["raw"]
        if not raw and desc_lines:
            raw = desc_lines.pop(0)
        theme_text, inline_rest = _split_prose_theme(raw)
        raw = theme_text or raw
        prose = " ".join(p for p in ([inline_rest] + desc_lines) if p).strip()
        nodes = _split_route_nodes(raw)
        cleaned_nodes = [_clean_place_token(n) for n in nodes if _clean_place_token(n)]
        place_pool.extend(cleaned_nodes)

        loc_text = " → ".join(nodes)
        theme = raw.replace("-", " · ").replace("→", " · ")
        overnight = cleaned_nodes[-1] if cleaned_nodes else "-"

        if "到达" in raw or "抵达" in raw:
            transport = "接机/接站"
        elif "离开" in raw or "返程" in raw:
            transport = "送机/送站"
        else:
            transport = "包车"

        meals = "早（酒店）" if day_no > 1 else "自理"

        itinerary_rows.append(
            {
                "day": day_no,
                "date": f"Day{day_no}",
                "title": theme,
                "locations": loc_text,
                "transport": transport,
                "distance": "待确认",
                "accommodation": overnight,
                "meals": meals,
            }
        )

        slot_base = cleaned_nodes[0] if cleaned_nodes else raw
        if prose:
            day_description = prose
        else:
            day_description = (
                f"{raw}。{DEFAULT_DAILY_DESCRIPTION_BOILERPLATE}；"
                "具体出发时刻与停留时长可按团队偏好微调。"
            )
        daily_components.append(
            {
                "type": "daily",
                "id": f"daily-{day_no:03d}",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": f"{slot_base} 旅行风光"},
                    "title": "详细行程",
                    "date": f"Day{day_no}",
                    "theme": theme,
                    "description": day_description,
                    "topImages": [
                        {"alternates": [], "slotLabel": f"{slot_base} 实拍"},
                        {"alternates": [], "slotLabel": f"{slot_base} 地标"},
                        {"alternates": [], "slotLabel": f"{slot_base} 氛围"},
                    ],
                    "sideImage": {"alternates": [], "slotLabel": f"{slot_base} 全景"},
                    "bottomImages": [
                        {"alternates": [], "slotLabel": f"{slot_base} 人文"},
                    ],
                },
            }
        )

    unique_places: List[str] = []
    seen = set()
    for p in place_pool:
        key = p.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique_places.append(p)

    main_places = unique_places[:4] if unique_places else ["定制行程"]
    trip_title = " · ".join(main_places)
    # 展示标题：优先用输入开头的整行标题（如「济南3日｜泉城慢旅」）；否则用动线拼接
    if doc_title:
        meta_title = doc_title
        cover_title = doc_title
    else:
        meta_title = f"{trip_title}{len(day_items)}日路书"
        cover_title = f"{trip_title} {len(day_items)}天路书"

    highlights = [
        f"{len(day_items)}天行程：{' → '.join(main_places)}",
        "支持按客户画像（亲子/长者/摄影/团建）快速微调",
        "路书数据结构支持在线编辑与二次销售复用",
    ]

    for key in ("用车", "门票", "住宿"):
        if sections.get(key):
            highlights.append(f"{key}：{sections[key]}")

    hotel_items = []
    for city, hotels in hotel_by_city.items():
        level_note = hotel_level_by_city.get(city, "")
        desc = _format_hotel_feature_description(city, hotels, level_note)
        primary = hotels[0] if hotels else city
        hotel_items.append(
            {
                "title": city,
                "description": desc,
                "images": [
                    {
                        "alternates": [],
                        "slotLabel": f"{primary} 酒店 外观 大堂",
                    },
                    {
                        "alternates": [],
                        "slotLabel": f"{primary} 酒店 客房 房型 早餐",
                    },
                ],
            }
        )

    if not hotel_items and sections.get("住宿"):
        raw = sections["住宿"].strip()
        hotels_fb, level_fb = _split_hotel_options(raw)
        if not hotels_fb and raw:
            hotels_fb = [raw]
        desc = _format_hotel_feature_description("", hotels_fb, level_fb)
        primary = hotels_fb[0] if hotels_fb else "酒店"
        hotel_items.append(
            {
                "title": "全程住宿",
                "description": desc,
                "images": [
                    {"alternates": [], "slotLabel": f"{primary} 酒店 外观 大堂"},
                    {"alternates": [], "slotLabel": f"{primary} 酒店 客房 房型"},
                ],
            }
        )

    service_sections: List[Dict] = []
    for heading in SERVICE_SECTION_HEADINGS_ORDER:
        if sections.get(heading):
            service_sections.append({"heading": heading, "body": _format_section_body(heading, sections[heading])})

    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    cost_sections = []
    has_quote_included = bool(sections.get("报价包含"))
    has_quote_excluded = bool(sections.get("报价不包含"))
    skip_when_quote = (
        {"用车", "门票", "保险", "其他", "餐食", "导服", "住宿"}
        if has_quote_included
        else set()
    )
    skip_when_excluded = {"不含", "费用不含"} if has_quote_excluded else set()

    for key in ("用车", "住宿", "门票", "保险", "餐食", "导服", "其他", "不含", "费用不含"):
        if key in skip_when_quote or key in skip_when_excluded:
            continue
        if sections.get(key):
            cost_sections.append({"heading": key, "body": _format_section_body(key, sections[key])})

    price_line = []
    if "adult" in prices:
        price_line.append(f"成人：¥{prices['adult']}/人")
    if "child" in prices:
        price_line.append(f"儿童：¥{prices['child']}/人")
    if price_line:
        cost_sections.insert(0, {"heading": "行程报价", "body": _format_section_body("行程报价", "；".join(price_line))})

    data = {
        "meta": {
            "version": "2.0",
            "title": meta_title,
            "createdAt": now,
            "updatedAt": now,
            "generationDate": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        "cover": {
            "backgroundImage": {"alternates": [], "slotLabel": f"{trip_title} 旅行风光"},
            "title": cover_title,
            "subtitle": "",
            "logo": {
                "slotLabel": "品牌 LOGO",
                "url": brand_logo_relative_url(),
                "alternates": [],
            },
        },
        "components": [
            {
                "type": "highlights",
                "id": "highlights-001",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": f"{trip_title} 行程亮点"},
                    "title": "行程亮点",
                    "items": highlights,
                },
            },
            {
                "type": "itinerary",
                "id": "itinerary-001",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": f"{trip_title} 路线图"},
                    "title": "行程概览",
                    "style": "table",
                    "items": itinerary_rows,
                },
            },
            *daily_components,
        ],
    }

    # 始终输出住宿模块；简表未解析到酒店时保留空卡片，供顾问后续填写
    if not hotel_items:
        hotel_items = [dict(EMPTY_HOTEL_FEATURE_ITEM)]

    data["components"].append(
        {
            "type": "feature",
            "id": "feature-hotel-001",
            "data": {
                "backgroundImage": {"alternates": [], "slotLabel": "酒店 房型 设施"},
                "title": "住宿安排",
                "subtype": "住宿",
                "items": hotel_items,
            },
        }
    )

    vehicle_desc = sections.get("用车", "")
    if vehicle_desc:
        data["components"].append(
            {
                "type": "feature",
                "id": "feature-transport-001",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": "包车 旅游用车"},
                    "title": "交通安排",
                    "subtype": "交通",
                    "items": [
                        {
                            "title": "用车服务",
                            "description": vehicle_desc,
                            "images": [{"alternates": [], "slotLabel": "旅游用车"}],
                        }
                    ],
                },
            }
        )

    if cost_sections or any(sections.get(h) for h in QUOTE_BLOCK_NAMES):
        cost_lines = _cost_sections_to_lines(cost_sections)
        for heading in QUOTE_BLOCK_NAMES:
            body = sections.get(heading, "").strip()
            if not body:
                continue
            cost_lines.append(heading)
            for ln in body.splitlines():
                ln = ln.strip()
                if ln:
                    cost_lines.append(ln)
        cost_ul = _lines_to_ul_html(cost_lines)
        data["components"].append(
            {
                "type": "text-block",
                "id": "text-cost-001",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": "费用说明"},
                    "title": "费用说明",
                    "subtype": "费用",
                    "content": cost_ul,
                },
            }
        )

    if service_sections:
        service_lines_parsed = _cost_sections_to_lines(service_sections)
        service_ul = _lines_to_ul_html(service_lines_parsed)
        data["components"].append(
            {
                "type": "text-block",
                "id": "text-service-001",
                "data": {
                    "backgroundImage": {"alternates": [], "slotLabel": "服务说明"},
                    "title": "服务说明",
                    "subtype": "服务",
                    "content": service_ul,
                },
            }
        )

    ensure_default_service_text_block(data)
    return data


def extract_sections_from_text(raw_text: str) -> Dict[str, str]:
    """从自由行简表文案中提取费用明细键值与服务类标题行（不要求含 D1/D2 行程行）。

    规则与 ``parse_brief`` 一致，供 ``merge_intake_fee_service`` 写入已有 tripData。
    """
    return parse_fee_sections_from_text(raw_text)


def write_trip_data(output_dir: Path, trip_data: Dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "tripData.json"
    path.write_text(json.dumps(trip_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render_html(trip_data_path: Path, output_dir: Path, html_name: str, image_registry: Path | None) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    generate_py = project_root / "assets" / "generate.py"
    html_path = output_dir / html_name

    cmd = [
        sys.executable,
        str(generate_py),
        str(trip_data_path),
        str(html_path),
        "--template",
        "roadbook-v2",
        "--auto-images",
        "--localize-images",
        "--no-serve",
        "--no-open",
    ]
    if image_registry:
        cmd.extend(["--image-registry", str(image_registry)])

    subprocess.run(cmd, check=True)
    return html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert itinerary brief text into roadbook-v2 tripData.")
    parser.add_argument("--input", required=True, help="Path to itinerary brief file (.txt/.md/.docx)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--render", action="store_true", help="Render HTML via assets/generate.py")
    parser.add_argument("--html-name", default="路书.html", help="Output HTML file name when --render is set")
    parser.add_argument(
        "--image-registry",
        default="assets/image_registry.sample.json",
        help="Image registry path for auto-image mode",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    raw = _read_text(input_path)
    trip_data = parse_brief(raw)
    trip_data_path = write_trip_data(output_dir, trip_data)
    print(f"tripData written: {trip_data_path}")

    if args.render:
        image_registry = Path(args.image_registry).resolve() if args.image_registry else None
        html_path = render_html(trip_data_path, output_dir, args.html_name, image_registry)
        print(f"HTML written: {html_path}")


if __name__ == "__main__":
    main()
