#!/usr/bin/env python3
"""从小红书（TikHub API）拉笔记正文，重写 roadbook v2 ``daily.data.description``。

依赖：仓库根 ``.env`` 中 ``TIKHUB_API_KEY``（与 ``fill_xhs_images.py`` 相同）；飞猪降级依赖本机 ``flyai`` CLI。

逻辑：
  - 按 ``xhs_search_keyword_rules.daily_enrich_description_keywords`` 生成检索词，TikHub search_notes → get_note_info；
  - 抽取笔记正文片段，过滤导流/硬广等劣质句式；
  - **素材不足时（默认正文总长 <80）**：按当日 ``theme`` 拆出景点名，依次用 **飞猪 FlyAI ``search-poi``**（多城市轮询）拉取结构化文案；仍不足则用 **中文维基百科摘要**（开放 API，无需密钥）；
  - **总是写入** ``description``（``synthesize_description`` 保证达到 ``--min-chars``，默认 200 字）；
  - **默认**：若设置 ``OPENAI_API_KEY`` 或 ``DEEPSEEK_API_KEY``，调用 OpenAI 兼容 **Chat Completions** 将素材改写成统一顾问口吻（约 ``--min-chars``–``--max-chars`` 字）；否则用 **overview + 洁净参考句** 合成顾问文风（非小红书原文拼接）；
  - ``--no-llm`` 强制不用 LLM；含 ``[话题]``、emoji、机位编号等 **劣质正文** 或 ``--force`` 时强制重写。

环境变量（可选 LLM）：
  - **优先** ``OPENAI_API_KEY`` + ``OPENAI_BASE_URL``（默认 ``https://api.openai.com/v1``）+ ``OPENAI_MODEL``（默认 ``gpt-4o-mini``）。
  - **仅 DeepSeek**：设 ``DEEPSEEK_API_KEY`` 即可（脚本默认 ``DEEPSEEK_BASE_URL=https://api.deepseek.com/v1``、``DEEPSEEK_MODEL=deepseek-chat``）；亦可显式覆盖 ``DEEPSEEK_BASE_URL`` / ``DEEPSEEK_MODEL``。密钥可写在仓库根 ``.env``，由 ``repo_dotenv.load_repo_dotenv`` 在脚本启动时自动加载（不覆盖已 export 的变量）。详见 ``docs/deepseek-llm-setup.md``。

用法:
  python3 scripts/enrich_daily_descriptions_from_xhs.py \"路书目录/tripData.json\" \\
    --timeout-ms 240000

与 ``fill_xhs_images.py`` 相同：可设 ``ROADBOOK_FILL_XHS_COOLDOWN_MS``，在 TikHub 请求之间休眠，减轻 deliver 连续计费压力。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fill_xhs_images import resolve_throttle_ms, throttle_sleep_ms
from tikhub_xhs_feeds import ensure_tikhub_api_key, fetch_raw_note_snippets
from image_fallback_chain import flyai_city_candidates, flyai_search_poi_inner
from llm_openai_compat import default_chat_model_id, resolve_llm_http_config, strip_md_fence
from repo_dotenv import load_repo_dotenv
from roadbook_constants import DEFAULT_DAILY_DESCRIPTION_BOILERPLATE
from xhs_search_keyword_rules import daily_enrich_description_keywords, primary_destination_core

# 与 intake 模板一致的占位描述：识别后默认重写
BOILERPLATE_MARK = DEFAULT_DAILY_DESCRIPTION_BOILERPLATE

BAD_FRAGMENTS = (
    "私信",
    "关注我",
    "公众号",
    "微信号",
    "加vx",
    "加V",
    "扫码",
    "免费领取",
    "评论区",
    "戳主页",
    "链接在",
    "淘宝搜",
    "拼多多",
    "代购",
)

POS_HINTS_RE = re.compile(
    r"(松弛|度假|慢行|慵懒|治愈|烟火|晨雾|日落|灯火|湖畔|山野|森林|海风|老城|秘境|在地|"
    r"穿过|沿着|走进|俯瞰|藏在|隐于|伴着|迎着|漫步|放空|出片|节奏|闲逛|发呆|"
    r"光影|步道|观景台|风情|惬意|沉浸|氛围|味蕾|穿行|夜游|喀斯特|梯田|古镇|苗寨|瀑布|游船|徒步|咖啡|小吃|夜市)"
)

# 每日 description LLM 润色提示词（``OPENAI_API_KEY`` 时生效；与 skill.md「行程详解」角色对齐）
DAILY_DESCRIPTION_LLM_SYSTEM = """你是「玩点旅行」的资深定制路书编辑，为客人撰写「当日导读」单段正文。

【目标】让人读完觉得：好玩、有吸引力、值得去，同时心里踏实——知道今天大致怎么过、不会赶。

【文风】松弛度假感：慢下来、留出呼吸感，像在讲「今天可以这样舒服地过」。语气亲切、稳重，句子偏长但顺口；有轻量画面感（光线、步行尺度、停留理由、气味或声响），拒绝廉价煽情、空话、标题党。

【氛围词库】可自然选用 2–4 个与目的地相符的表达（勿堆砌）：松弛感、度假氛围、山野感、烟火气、晨雾、日落时分、灯火渐亮、街角咖啡馆、老城气息、慵懒午后、安静避世、城市夜色、森林气息、湖畔微风、沿街漫步、小众秘境、在地生活；海风仅用于海滨/海岛行程。

【动线动词】可穿插：穿过、沿着、走进、俯瞰、藏在、隐于、伴着、迎着、坐在、漫步于、被山谷包围、顺着河流前行、在夕阳下、清晨薄雾中、灯光渐亮时、推窗即可看到。

【节奏话术】在素材支持时体现：适合放空、适合慢慢逛、很容易出片、旅行节奏舒服、不会太赶、留出自由时间、适合发呆、氛围特别松弛、适合感受当地生活、真正进入目的地的节奏；可写「今天整体节奏相对轻松」「上午自然风光、下午自由闲逛」「避免长时间拉车」「晚间可早点回酒店休息」等——须与当日实际安排一致，无依据不写。

【硬性禁令】
- 只写「当日主题 / 行程概要 / 素材」中已出现的节点与活动；不得新增景点、餐厅、酒店、交通方式或具体时刻。
- 不得捏造票价、套餐、预约政策、闭馆信息；勿承诺行程未明示包含的项目。
- 禁止小红书腔（姐妹们、绝绝子、爆款、冲冲冲）、emoji、导流（关注/私信/扫码）。
- 输出仅为一段中文正文：不要 Markdown、不要标题、不要编号列表。"""

DAILY_DESCRIPTION_LLM_USER_RULES = """【准确性】下列素材可能有错漏；以「当日主题」「行程概要」为事实主干，参考素材仅作氛围与体验补充。冲突时以主题/概要为准，可删无关内容。

【叙事】按时间或动线自然串起当日 2–4 个核心节点（来自主题/概要），写清「为什么值得停留」与「怎么舒服地玩」，而非机械罗列地名。

【承诺边界】不写「一定」「保证」；可写「可按领队节奏微调」「以成团确认单为准」。

【字数】见下方目标区间；不足时可略写留白与自由活动时间，仍须扣住已有节点。"""

_XHS_JUNK_LINE_RE = re.compile(
    r"(\[话题\]|机位分享|旅游万粉|绝绝子|冲冲冲|姐妹们|戳主页|"
    r"P\d+P\d+|图\d+[）)]|一线：|Day\d{1,2}\d{1,2}月|"
    r"[0-9]️⃣|#\S+\[话题\])"
)
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]+")

_LLM_INFO_ONCE = False


def _log_llm_status_once(message: str) -> None:
    global _LLM_INFO_ONCE
    if _LLM_INFO_ONCE:
        return
    print(message, flush=True)
    _LLM_INFO_ONCE = True


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    n = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return n / len(text)


def _truncate_material(text: str, limit: int = 5500) -> str:
    t = text.strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "…"


def rewrite_daily_description_llm(
    *,
    trip_title: str,
    date_label: str,
    theme: str,
    raw_material: str,
    min_chars: int,
    max_chars: int,
    model: str,
    timeout_s: int,
) -> str | None:
    """OpenAI 兼容 ``/v1/chat/completions``；失败返回 None。"""
    api_key, base = resolve_llm_http_config()
    if not api_key:
        return None

    material = _truncate_material(raw_material)
    sys_prompt = DAILY_DESCRIPTION_LLM_SYSTEM
    user_prompt = (
        f"{DAILY_DESCRIPTION_LLM_USER_RULES}\n\n"
        f"整本路书标题：{trip_title or '（未提供）'}\n"
        f"当日序号：{date_label}\n"
        f"当日主题（途经点须全部来自此处，自然融入叙事）：{theme or '（未提供）'}\n\n"
        f"字数目标：约 {min_chars}–{max_chars} 字（中文）。\n\n"
        f"素材：\n{material}"
    )

    payload = {
        "model": model,
        "temperature": 0.62,
        "max_tokens": 900,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    url = f"{base}/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw_json = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            detail = ""
        print(f"WARN LLM HTTP {exc.code}: {detail or exc.reason}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"WARN LLM 请求失败: {exc}", file=sys.stderr)
        return None

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        print("WARN LLM 响应非 JSON", file=sys.stderr)
        return None
    if isinstance(data.get("error"), dict):
        print(f"WARN LLM API 错误: {data['error']}", file=sys.stderr)
        return None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print("WARN LLM 响应缺少 choices[0].message.content", file=sys.stderr)
        return None
    if not isinstance(content, str):
        return None

    out = strip_md_fence(content)
    out = re.sub(r"[\r\n]+", "", out)
    out = re.sub(r"[ \t]+", "", out)
    out = re.sub(r"。{2,}", "。", out).strip()

    if _cjk_ratio(out) < 0.25 and len(out) > 40:
        print("WARN LLM 输出中文占比过低，改用规则拼接", file=sys.stderr)
        return None
    low = out.lower()
    for b in BAD_FRAGMENTS:
        if b.lower() in low:
            print(f"WARN LLM 输出含导流词「{b}」，改用规则拼接", file=sys.stderr)
            return None
    if len(out) < max(min_chars - 25, 72):
        return None
    if len(out) > max_chars + 120:
        out = out[: max_chars + 80]
        cut = max(out.rfind("。"), out.rfind("！"), out.rfind("？"))
        if cut >= min_chars:
            out = out[: cut + 1]
    return out


def _walk_note_text(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            lk = str(k).lower()
            if isinstance(v, str) and len(v) > 35:
                if any(
                    x in lk
                    for x in (
                        "desc",
                        "content",
                        "title",
                        "text",
                        "abstract",
                        "notedesc",
                        "note_desc",
                    )
                ):
                    out.append(v.strip())
            _walk_note_text(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_note_text(item, out)


def extract_note_text(detail: Any) -> str:
    chunks: list[str] = []
    _walk_note_text(detail, chunks)
    seen: set[str] = set()
    ordered: list[str] = []
    for c in chunks:
        c = re.sub(r"#([^#\s]{1,32})#", r"\1", c)
        c = re.sub(r"https?://\S+", " ", c)
        c = re.sub(r"\s+", " ", c).strip()
        if len(c) < 40 or c in seen:
            continue
        seen.add(c)
        ordered.append(c)
    return " ".join(ordered)


def is_bad_sentence(s: str) -> bool:
    t = s.strip()
    if len(t) < 12:
        return True
    low = t.lower()
    for b in BAD_FRAGMENTS:
        if b.lower() in low:
            return True
    return is_xhs_junk_sentence(t)


def is_xhs_junk_sentence(s: str) -> bool:
    t = (s or "").strip()
    if len(t) < 8:
        return True
    if _XHS_JUNK_LINE_RE.search(t):
        return True
    if _EMOJI_RE.search(t):
        return True
    if t.count("#") >= 3:
        return True
    return False


def is_low_quality_description(desc: str) -> bool:
    """识别小红书腔拼接、话题标签、机位清单等不宜交付的正文。"""
    d = (desc or "").strip()
    if not d:
        return True
    if BOILERPLATE_MARK in d:
        return True
    if is_xhs_junk_sentence(d):
        return True
    if _XHS_JUNK_LINE_RE.search(d):
        return True
    if _EMOJI_RE.search(d):
        return True
    if d.count("[话题]") >= 1:
        return True
    if "一线：" in d and len(d) > 60:
        return True
    return False


def clean_xhs_blob(blob: str) -> str:
    t = (blob or "").strip()
    if not t:
        return ""
    t = re.sub(r"#([^#\s]{1,32})#", " ", t)
    t = re.sub(r"\[[^\]]*话题\]", " ", t)
    t = _EMOJI_RE.sub(" ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def build_llm_material(
    *,
    theme: str,
    overview: str,
    raw: str,
) -> str:
    parts: list[str] = []
    ov = (overview or "").strip()
    if ov and not is_low_quality_description(ov):
        parts.append(f"【行程概要】\n{ov}")
    if theme:
        parts.append(f"【当日主题】\n{theme}")
    cleaned = clean_xhs_blob(raw)
    if cleaned and len(cleaned) >= 40:
        parts.append(f"【参考素材】\n{cleaned}")
    return "\n\n".join(parts) if parts else clean_xhs_blob(raw) or raw


def split_sentences(blob: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*", blob)
    return [p.strip() for p in parts if p and len(p.strip()) >= 12]


def score_sentence(s: str) -> int:
    sc = len(s)
    if POS_HINTS_RE.search(s):
        sc += 40
    return sc


def _apply_description_length_guard(text: str, min_chars: int, max_chars: int) -> str:
    """统一字数守卫：去重连续句号 → 垫长至 min_chars → 按句末截断至 max_chars。"""
    text = re.sub(r"。{2,}", "。", text)
    guard = "动线与停留可根据当日车况与体力微调，请以领队与确认单为准。"
    while len(text) < min_chars:
        text += guard
        if len(text) >= min_chars:
            break
        guard = "建议留出拍照与小憩节奏，避免赶路透支体验。"
    if len(text) > max_chars:
        text = text[:max_chars]
        cut = max(text.rfind("。"), text.rfind("！"), text.rfind("？"))
        if cut >= min_chars - 20:
            text = text[: cut + 1]
    return text


def compose_advisor_description(
    *,
    theme: str,
    overview: str,
    date_label: str,
    trip_title: str,
    raw: str,
    min_chars: int,
    max_chars: int,
) -> str:
    """无 LLM 时：以 overview 为骨、洁净参考句为肉，输出顾问口吻单段正文。"""
    parts: list[str] = []
    ov = re.sub(r"\s+", "", (overview or "").strip())
    if ov and not is_low_quality_description(ov) and len(ov) >= 24:
        parts.append(ov if ov.endswith(("。", "！", "？")) else ov + "。")

    cleaned_raw = clean_xhs_blob(raw)
    sentences = split_sentences(cleaned_raw)
    ranked = sorted(
        ((score_sentence(s), s) for s in sentences if not is_bad_sentence(s)),
        reverse=True,
    )
    body_len = sum(len(p) for p in parts)
    for _, s in ranked:
        if body_len >= min_chars:
            break
        if any(s[: min(18, len(s))] in p for p in parts):
            continue
        sent = s if s.endswith(("。", "！", "？")) else s.rstrip("，、 ") + "。"
        parts.append(sent)
        body_len += len(sent)
        if body_len >= max_chars:
            break

    if not parts:
        hint = theme or (trip_title[:40] if trip_title else "当日行程")
        parts.append(
            f"围绕「{hint}」，建议把节奏放慢，留出驻足拍照与休整的空档；"
            "具体停留与接驳时刻以成团确认单为准。"
        )

    return _apply_description_length_guard("".join(parts), min_chars, max_chars)


def synthesize_description(
    raw: str,
    *,
    theme: str,
    date_label: str,
    trip_title: str,
    overview: str = "",
    min_chars: int,
    max_chars: int,
) -> str:
    if overview and not is_low_quality_description(overview):
        return compose_advisor_description(
            theme=theme,
            overview=overview,
            date_label=date_label,
            trip_title=trip_title,
            raw=raw,
            min_chars=min_chars,
            max_chars=max_chars,
        )
    sentences = split_sentences(clean_xhs_blob(raw) or raw)
    ranked = sorted(
        ((score_sentence(s), s) for s in sentences if not is_bad_sentence(s)),
        reverse=True,
    )

    picked: list[str] = []
    length = 0
    for _, s in ranked:
        if s in picked:
            continue
        if length + len(s) > max_chars + 80:
            continue
        picked.append(s)
        length += len(s)
        if length >= min_chars:
            break

    if not picked:
        picked = [s for _, s in ranked[:8] if s]

    body = "".join(picked)
    if not body:
        hint = theme or (trip_title[:40] if trip_title else "当日目的地")
        body = (
            f"围绕「{hint}」，适合把节奏放慢：留出驻足拍照与喝咖啡的空档，"
            "把体力留给一两处高光体验；其余交给转角的小吃与人声。"
            "若以自驾或包车衔接多日城市与景区，记得预留午休与傍晚弹性。"
        )

    if not body.endswith(("。", "！", "？")):
        body = body.rstrip("，、 ") + "。"

    text = body.strip()
    text = re.sub(r"\s+", "", text)
    return _apply_description_length_guard(text, min_chars, max_chars)


MIN_RAW_MATERIAL_CHARS = 80
_WIKI_UA = "RoadbookDailyEnrich/1.1 (skills-travel-planner; policy:https://meta.wikimedia.org/wiki/User-Agent_policy)"


def attraction_spots_from_theme(theme: str, data: dict[str, Any]) -> list[str]:
    """从每日 theme 拆出候选景点/区域名，供飞猪 search-poi 与维基检索。"""
    out: list[str] = []

    def add(s: str) -> None:
        t = re.sub(r"\s+", " ", (s or "").strip())
        if len(t) < 2:
            return
        if t not in out:
            out.append(t)

    t = (theme or "").strip()
    if t:
        for part in re.split(r"[·•、，,—\-–→]+", t):
            add(part)
        if not out:
            add(t)
    core = primary_destination_core(data)
    if core:
        add(core)
    return out[:10]


def _walk_poi_item_for_text(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            lk = str(k).lower()
            if isinstance(v, str) and len(v.strip()) > 8:
                if any(
                    x in lk
                    for x in (
                        "name",
                        "addr",
                        "address",
                        "desc",
                        "content",
                        "title",
                        "text",
                        "abstract",
                        "intro",
                        "summary",
                        "tip",
                        "poi",
                        "detail",
                        "remark",
                        "interest",
                        "scenic",
                        "comment",
                        "tag",
                        "level",
                        "area",
                        "city",
                    )
                ):
                    out.append(v.strip())
            _walk_poi_item_for_text(v, out)
    elif isinstance(node, list):
        for item in node[:24]:
            _walk_poi_item_for_text(item, out)


def flyai_poi_items_to_text(inner: dict[str, Any] | None) -> str:
    if not isinstance(inner, dict):
        return ""
    il = inner.get("itemList")
    if not isinstance(il, list):
        return ""
    blocks: list[str] = []
    for item in il[:8]:
        if not isinstance(item, dict):
            continue
        acc: list[str] = []
        _walk_poi_item_for_text(item, acc)
        blob = re.sub(r"\s+", " ", " ".join(acc)).strip()
        blob = re.sub(r"https?://\S+", " ", blob)
        if len(blob) > 24:
            blocks.append(blob)
    return "\n".join(blocks)


def fetch_wikipedia_zh_extract(keyword: str, timeout: float = 14.0) -> str:
    """中文维基「摘要」PlainText，失败返回空串。"""
    q = (keyword or "").strip()
    if len(q) < 2:
        return ""
    op = "https://zh.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": q,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        }
    )
    req = urllib.request.Request(op, headers={"User-Agent": _WIKI_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, list) or len(data) < 2:
        return ""
    titles = data[1]
    if not isinstance(titles, list) or not titles:
        return ""
    title = str(titles[0])
    qp = "https://zh.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "titles": title,
        }
    )
    req2 = urllib.request.Request(qp, headers={"User-Agent": _WIKI_UA})
    try:
        with urllib.request.urlopen(req2, timeout=timeout) as resp:
            blob = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return ""
    pages = ((blob or {}).get("query") or {}).get("pages")
    if not isinstance(pages, dict):
        return ""
    for _, page in pages.items():
        if not isinstance(page, dict) or page.get("missing"):
            continue
        ex = page.get("extract")
        if isinstance(ex, str) and len(ex.strip()) > 40:
            line = re.sub(r"\s+", " ", ex.strip())
            line = re.sub(r"https?://\S+", " ", line)
            return line
    return ""


def gather_daily_raw_material(
    root: Path,
    data: dict[str, Any],
    comp_index: int,
    theme: str,
    xhs_keywords: list[str],
    *,
    timeout_ms: int,
    max_notes: int,
    retries: int,
    flyai_timeout: int,
    flyai_fallback: bool,
    web_fallback: bool,
) -> str:
    """小红书笔记 →（不足则）飞猪 POI →（仍不足则）维基摘要。"""
    raw_xhs = fetch_raw_snippets(root, xhs_keywords, timeout_ms, max_notes=max_notes, retries=retries)
    pieces: list[str] = []
    if raw_xhs.strip():
        pieces.append(raw_xhs.strip())

    merged_len = len("\n".join(pieces))
    if merged_len >= MIN_RAW_MATERIAL_CHARS:
        return "\n".join(pieces)

    spots = attraction_spots_from_theme(theme, data)
    cities = flyai_city_candidates(data, ["components", comp_index], (theme or (spots[0] if spots else "")).strip())
    flyai_texts: list[str] = []
    if flyai_fallback and spots:
        for spot in spots:
            blob_acc = ""
            for city in cities:
                inner = flyai_search_poi_inner(city, spot, flyai_timeout)
                blob = flyai_poi_items_to_text(inner or {})
                if len(blob) >= 40:
                    blob_acc = blob
                    print(f"  [flyai poi] city={city!r} spot={spot!r} -> {len(blob)} 字", flush=True)
                    break
            if blob_acc:
                flyai_texts.append(blob_acc)
            if sum(len(x) for x in flyai_texts) >= 500:
                break
        if flyai_texts:
            pieces.extend(flyai_texts)

    merged = "\n".join(pieces).strip()
    if len(merged) >= MIN_RAW_MATERIAL_CHARS:
        return merged

    if web_fallback and spots:
        wiki_chunks: list[str] = []
        for spot in spots[:6]:
            ex = fetch_wikipedia_zh_extract(spot)
            if len(ex) > 60:
                wiki_chunks.append(ex)
                print(f"  [wiki] spot={spot!r} -> {len(ex)} 字", flush=True)
            if sum(len(x) for x in wiki_chunks) >= 900:
                break
        if sum(len(x) for x in wiki_chunks) < MIN_RAW_MATERIAL_CHARS:
            core = primary_destination_core(data)
            combo = f"{core}{spots[0]}" if core and spots else ""
            if combo and len(combo) >= 3:
                ex2 = fetch_wikipedia_zh_extract(combo)
                if len(ex2) > 60:
                    wiki_chunks.append(ex2)
                    print(f"  [wiki] combo={combo!r} -> {len(ex2)} 字", flush=True)
        if wiki_chunks:
            pieces.extend(wiki_chunks)

    return "\n".join(pieces).strip()


def fetch_raw_snippets(
    root: Path,
    keywords: list[str],
    timeout_ms: int,
    *,
    max_notes: int,
    retries: int,
) -> str:
    cooldown_ms = resolve_throttle_ms(-1, "ROADBOOK_FILL_XHS_COOLDOWN_MS", 0)
    return fetch_raw_note_snippets(
        root,
        keywords,
        timeout_ms,
        max_notes=max_notes,
        retries=retries,
        extract_text=extract_note_text,
        throttle_sleep_ms=lambda ms: throttle_sleep_ms(ms or cooldown_ms),
    )


def should_rewrite(desc: str, min_chars: int, force: bool) -> bool:
    if force:
        return True
    d = (desc or "").strip()
    if is_low_quality_description(d):
        return True
    return len(d) < min_chars


def enrich_trip(
    data: dict[str, Any],
    root: Path,
    *,
    min_chars: int,
    max_chars: int,
    timeout_ms: int,
    force: bool,
    max_notes: int,
    retries: int,
    limit_days: int,
    dry_run: bool,
    use_llm: bool,
    llm_model: str,
    llm_timeout_s: int,
    flyai_timeout: int,
    flyai_fallback: bool,
    web_fallback: bool,
) -> int:
    meta_title = str((data.get("meta") or {}).get("title") or "").strip()
    updated = 0
    comps = data.get("components")
    if not isinstance(comps, list):
        return 0

    daily_indices = [i for i, c in enumerate(comps) if isinstance(c, dict) and c.get("type") == "daily"]
    if limit_days > 0:
        daily_indices = daily_indices[:limit_days]

    if use_llm and resolve_llm_http_config()[0]:
        oa = (os.environ.get("OPENAI_API_KEY") or "").strip()
        label = "OPENAI_API_KEY" if oa else "DEEPSEEK_API_KEY"
        _log_llm_status_once(
            f"INFO: 已配置 {label}，每日 description 将经 OpenAI 兼容 Chat Completions 统一文风（可用 --no-llm 关闭）"
        )
    elif use_llm:
        _log_llm_status_once(
            "INFO: 未设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY，每日 description 使用 overview+素材顾问合成"
        )
    else:
        _log_llm_status_once("INFO: --no-llm：每日 description 仅用顾问合成（不用 LLM）")

    for ord_, i in enumerate(daily_indices):
        comp = comps[i]
        dd = comp.get("data") if isinstance(comp.get("data"), dict) else {}
        theme = str(dd.get("theme") or "").strip()
        date_label = str(dd.get("date") or "").strip() or "当日"
        overview = str(dd.get("overview") or "").strip()
        desc = str(dd.get("description") or "")

        if not should_rewrite(desc, min_chars, force):
            print(f"skip daily[{i}] {date_label}: 已有文案 ({len(desc)} 字)", flush=True)
            continue

        kws = daily_enrich_description_keywords(data, theme, daily_ordinal=ord_)
        print(f"enrich daily[{i}] {date_label} {theme!r} keywords={kws[:3]}…", flush=True)

        if dry_run:
            continue

        raw = gather_daily_raw_material(
            root,
            data,
            i,
            theme,
            kws,
            timeout_ms=timeout_ms,
            max_notes=max_notes,
            retries=retries,
            flyai_timeout=flyai_timeout,
            flyai_fallback=flyai_fallback,
            web_fallback=web_fallback,
        )
        if len(raw) < MIN_RAW_MATERIAL_CHARS:
            print(
                f"WARN daily[{i}] 聚合素材仍较短（{len(raw)} 字），将用规则/模板垫长至 {min_chars} 字",
                file=sys.stderr,
            )

        llm_material = build_llm_material(theme=theme, overview=overview, raw=raw)
        draft = compose_advisor_description(
            theme=theme,
            overview=overview,
            date_label=date_label,
            trip_title=meta_title,
            raw=raw,
            min_chars=min_chars,
            max_chars=max_chars,
        )
        new_desc = draft
        llm_out: str | None = None
        if use_llm:
            llm_out = rewrite_daily_description_llm(
                trip_title=meta_title,
                date_label=date_label,
                theme=theme,
                raw_material=llm_material,
                min_chars=min_chars,
                max_chars=max_chars,
                model=llm_model,
                timeout_s=llm_timeout_s,
            )
            if llm_out:
                new_desc = llm_out

        dd["description"] = new_desc
        updated += 1
        src = "LLM" if llm_out else "顾问合成"
        print(f"  -> {len(new_desc)} 字 [{src}]", flush=True)

    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description="小红书笔记 → daily.description")
    ap.add_argument("trip_json", help="tripData.json")
    ap.add_argument("--min-chars", type=int, default=200, help="最短字数（默认 200）")
    ap.add_argument("--max-chars", type=int, default=400, help="最长字数软上限（默认 400）")
    ap.add_argument("--timeout-ms", type=int, default=240000, help="单次 MCP 超时毫秒")
    ap.add_argument("--force", action="store_true", help="忽略已有长文案，强制重写")
    ap.add_argument(
        "--max-notes",
        type=int,
        default=4,
        help="每个 daily 最多拉取几条笔记正文拼素材（默认 4）",
    )
    ap.add_argument("--slot-retries", type=int, default=2, help="每个关键词失败重试次数")
    ap.add_argument("--limit-days", type=int, default=0, help="仅处理前 N 个 daily，0 为全部")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--flyai-timeout",
        type=int,
        default=55,
        metavar="SEC",
        help="飞猪 search-poi 子进程超时秒（默认 55）",
    )
    ap.add_argument(
        "--no-flyai-fallback",
        action="store_true",
        help="禁用飞猪 POI 正文降级（仅小红书 + 维基）",
    )
    ap.add_argument(
        "--no-web-fallback",
        action="store_true",
        help="禁用维基摘要降级（仅小红书 + 飞猪）",
    )
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="禁用 LLM，仅用顾问合成（不读取 OPENAI_API_KEY / DEEPSEEK_API_KEY）",
    )
    ap.add_argument(
        "--llm-model",
        default="",
        metavar="ID",
        help="Chat Completions 模型 id；默认 OPENAI_MODEL / DEEPSEEK_MODEL / gpt-4o-mini / deepseek-chat",
    )
    ap.add_argument(
        "--llm-timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="LLM HTTP 超时秒（默认 120）",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    load_repo_dotenv(root)
    path = Path(args.trip_json).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))

    llm_model = (args.llm_model or "").strip() or default_chat_model_id()

    if not args.dry_run:
        ensure_tikhub_api_key(root)

    from tikhub_xhs_cache import XhsNoteCache, cache_path_for_trip, set_active_cache

    xhs_cache: XhsNoteCache | None = None
    if not args.dry_run:
        xhs_cache = XhsNoteCache(cache_path_for_trip(path))
        set_active_cache(xhs_cache)

    n = enrich_trip(
        data,
        root,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        timeout_ms=args.timeout_ms,
        force=args.force,
        max_notes=args.max_notes,
        retries=args.slot_retries,
        limit_days=args.limit_days,
        dry_run=args.dry_run,
        use_llm=not args.no_llm,
        llm_model=llm_model,
        llm_timeout_s=args.llm_timeout,
        flyai_timeout=args.flyai_timeout,
        flyai_fallback=not args.no_flyai_fallback,
        web_fallback=not args.no_web_fallback,
    )

    if not args.dry_run and n:
        meta = data.setdefault("meta", {})
        meta["updatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if xhs_cache is not None:
        try:
            xhs_cache.save()
            print(f"[xhs-cache] {xhs_cache.stats_line()} → {xhs_cache.path}", flush=True)
        except OSError as exc:
            print(f"WARN xhs-cache save failed: {exc}", file=__import__("sys").stderr)

    print(f"已更新 {n} 条每日描述 → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
