"""Roadbook v2 小红书 ``search_feeds`` 检索词策略（按槽位类型 + tripData 上下文）。

与 ``fill_xhs_images.py`` 配合：结构化关键词优先，地理/扩展词兜底。
"""

from __future__ import annotations

import re
from typing import Any


_MAX_KEYWORD_CHARS = 24  # 小红书 search_feeds 经验值：超过 24 字符召回率显著下降
_HOTEL_QUALIFIERS = {"外观", "大堂", "客房", "房型", "早餐", "设施", "酒店", "实拍", "民宿", "入住", "体验"}
_DESTINATION_SPLIT_RE = re.compile(r"[·•、，]")

# 按「当日在路书中是第几个 daily」轮换：降低多日共用「贵阳 攻略」等同质词导致的笔记撞车。
_DAILY_SUFFIX_GROUPS = (
    (" 攻略", " 风景", " 描述"),
    (" 游记", " 实拍", " 打卡"),
    (" 玩法", " 路线", " 小众"),
)


def _normalize_kw(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("小红书", " ")).strip()


def _trim_destination(text: str) -> str:
    """从 title / slot_label 抽出头部目的地短语：切 ·、，，剩剩剩脱天数前后缀。"""
    t = re.sub(r"\s*[·•]\s*", "·", text or "").strip()
    first = _DESTINATION_SPLIT_RE.split(t, maxsplit=1)[0].strip()
    first = re.sub(
        r"^[一二三四五六七八九十零〇百千两\d]+\s*天\s*[一二三四五六七八九十零〇百千两\d]+\s*晚\s*",
        "",
        first,
    )
    first = re.sub(r"^\d+天\d+晚\s*", "", first)
    first = re.sub(r"\s*\d+\s*天\s*$", "", first)  # 脱尾部「N 天」
    return first[:48]


def _within_length(seq: list[str]) -> list[str]:
    return [s for s in seq if 0 < len(s) <= _MAX_KEYWORD_CHARS]


def primary_destination_core(data: dict[str, Any]) -> str:
    """从 meta.title / cover.title 抽取行程主目的地短语（短）。"""
    meta = data.get("meta") or {}
    title = str(meta.get("title") or "").strip()
    if title:
        first = _trim_destination(title)
        if len(first) >= 2:
            return first
    cover = data.get("cover") or {}
    bt = str(cover.get("title") or "").strip()
    if bt:
        first = _trim_destination(bt)
        if len(first) >= 2:
            return first
    return ""


def component_index_from_path(path: list[Any]) -> int | None:
    try:
        i = path.index("components")
        if i + 1 < len(path) and isinstance(path[i + 1], int):
            return path[i + 1]
    except ValueError:
        pass
    return None


def get_component(data: dict[str, Any], idx: int) -> dict[str, Any] | None:
    comps = data.get("components")
    if isinstance(comps, list) and 0 <= idx < len(comps):
        c = comps[idx]
        return c if isinstance(c, dict) else None
    return None


def daily_ordinal_from_path(data: dict[str, Any], path: list[Any]) -> int:
    """当前槽所属 ``daily`` 组件在全书 daily 列表中的序号（0-based），用于检索词轮换。"""
    ci = component_index_from_path(path)
    if ci is None:
        return 0
    comps = data.get("components")
    if not isinstance(comps, list):
        return 0
    daily_ix = [j for j, c in enumerate(comps) if isinstance(c, dict) and c.get("type") == "daily"]
    try:
        return daily_ix.index(ci)
    except ValueError:
        return 0


def _rotate_bases(bases: list[str], daily_ordinal: int) -> list[str]:
    if len(bases) <= 1:
        return list(bases)
    k = daily_ordinal % len(bases)
    return bases[k:] + bases[:k]


def _condensed_theme_base(theme: str) -> str:
    """整段行程名称压成一条短语（空格代 ·），优先于切段检索，减少只搜「枢纽城」撞笔记。"""
    t = _normalize_kw((theme or "").replace("·", " ").replace("•", " "))
    if not t:
        return ""
    max_b = max(2, _MAX_KEYWORD_CHARS - 4)
    if len(t) > max_b:
        return t[:max_b].rstrip(" ·•、，")
    return t


def _daily_search_keyword_matrix(
    bases: list[str],
    *,
    daily_ordinal: int,
    theme_line: str = "",
) -> list[str]:
    """切段轮换顺序 + 按天轮换后缀组 + 整段主题优先。"""
    suf_group = _DAILY_SUFFIX_GROUPS[daily_ordinal % len(_DAILY_SUFFIX_GROUPS)]
    rb = _rotate_bases(bases, daily_ordinal)
    out: list[str] = []
    condensed = _condensed_theme_base(theme_line) if theme_line else ""
    if condensed:
        for suf in suf_group:
            k = _keyword_with_suffix(condensed, suf)
            if k:
                out.append(k)
    for b in rb:
        for suf in suf_group:
            k = _keyword_with_suffix(b, suf)
            if k:
                out.append(k)
    return out


def daily_data_for_path(data: dict[str, Any], path: list[Any]) -> dict[str, Any] | None:
    ci = component_index_from_path(path)
    if ci is None:
        return None
    comp = get_component(data, ci)
    if not isinstance(comp, dict) or comp.get("type") != "daily":
        return None
    dd = comp.get("data")
    return dd if isinstance(dd, dict) else None


def classify_image_slot(data: dict[str, Any], path: list[Any]) -> str:
    """槽位意图分类，用于检索词模板。"""
    if len(path) >= 2 and path[0] == "cover" and path[1] == "backgroundImage":
        return "cover_bg"

    ci = component_index_from_path(path)
    if ci is None:
        return "generic"
    comp = get_component(data, ci)
    if not isinstance(comp, dict):
        return "generic"
    ctype = comp.get("type")

    if ctype in ("highlights", "itinerary"):
        if path and path[-1] == "backgroundImage":
            return "section_hero"

    if ctype == "daily":
        try:
            di = path.index("data")
            if di + 1 < len(path):
                dk = path[di + 1]
                if dk in ("backgroundImage", "sideImage", "topImages", "bottomImages"):
                    return "daily_activity"
        except ValueError:
            pass

    if ctype == "feature":
        dd = comp.get("data") if isinstance(comp.get("data"), dict) else {}
        st = dd.get("subtype")
        if st == "住宿" and "images" in path:
            return "hotel_gallery"
        if st == "交通" and "images" in path:
            return "transport_gallery"
        # feature 自身的 backgroundImage（住宿/交通/餐饮/购物等板块总背景图）——按目的地+主题搜，避免仅靠「酒店 房型 设施」这种泛词
        if path and path[-1] == "backgroundImage":
            return "feature_section_bg"

    return "generic"


def fallback_geo_variants(primary: str, data: dict[str, Any] | None = None) -> list[str]:
    """地理与泛化扩展：从 tripData 推断目的地后缀，避免硬编码省份污染非西南路书。"""
    p = _normalize_kw(primary)
    if not p:
        return []
    suffixes: list[str] = [""]
    if data is not None:
        core = primary_destination_core(data)
        if core:
            tokens = [t.strip() for t in re.split(r"[·•、，\s]+", core) if t.strip()]
            for tok in tokens[:2]:
                if len(tok) >= 2 and tok not in p:
                    suffixes.append(f" {tok}")
    suffixes.append(" 旅行")

    out: list[str] = []
    seen: set[str] = set()
    for suf in suffixes:
        k = (p + suf).strip() if suf else p
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _dedupe_preserve(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        x = x.strip()
        if len(x) < 2 or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _keyword_with_suffix(base: str, suffix: str) -> str:
    """拼接 ``base + suffix``，总长度不超过 ``_MAX_KEYWORD_CHARS``（小红书召回经验值）。"""
    b = (base or "").strip()
    if not b:
        return ""
    suf = suffix if suffix.startswith(" ") else f" {suffix}"
    max_b = _MAX_KEYWORD_CHARS - len(suf)
    if max_b < 2:
        return ""
    if len(b) > max_b:
        b = b[:max_b].rstrip(" ·•、，")
    if len(b) < 2:
        return ""
    return f"{b}{suf}"


def _theme_search_bases(theme: str) -> list[str]:
    """从每日行程名称（``theme``）拆出检索主干：按 ·• 切段，去重保序。"""
    t = _normalize_kw(theme)
    if not t:
        return []
    parts = [p.strip() for p in re.split(r"[·•]", t) if p.strip()]
    if not parts:
        parts = [t]
    out: list[str] = []
    for p in parts[:6]:
        if len(p) < 2:
            continue
        out.append(p)
    return _dedupe_preserve(out)


def hero_landmark_keywords(data: dict[str, Any], slot_label: str) -> list[str]:
    """封面 / 行程亮点 / 行程概览等大标题背景：目的地感 + 风光航拍。

    优化点：
    - slot_label 过长（含多个 · / 、 / ，）时只取首段，避免出现 30+ 字符超长关键词。
    - 去除「建筑 大气」模板（对自然/民俗类目的地会跑偏），统一用「风光/航拍」。
    """
    lab = _normalize_kw(_trim_destination(slot_label) if slot_label else "")
    core = primary_destination_core(data)
    out: list[str] = []
    bases: list[str] = []
    if core:
        bases.append(core)
    if lab and lab != core:
        bases.append(lab)
    bases = _dedupe_preserve(bases)
    for b in bases:
        out.append(f"{b} 风光 大气")
        out.append(f"{b} 航拍 风景")
    if core and lab and lab != core:
        out.append(f"{core} {lab} 航拍 风光")
    return _dedupe_preserve(out)


def daily_enrich_description_keywords(
    data: dict[str, Any], theme: str, *, daily_ordinal: int = 0
) -> list[str]:
    """每日 ``description`` 文本素材：整段 ``theme`` 优先 + 切段顺序按日轮换 + 后缀三组轮换，减少多日搜到同一批笔记。"""
    t = _normalize_kw(theme)
    bases = _theme_search_bases(t) if t else []
    out: list[str] = []
    if bases:
        out.extend(_daily_search_keyword_matrix(bases, daily_ordinal=daily_ordinal, theme_line=t))
    core = primary_destination_core(data)
    if core and bases:
        rb = _rotate_bases(bases, daily_ordinal)
        combo = _keyword_with_suffix(f"{core} {rb[0]}", " 攻略")
        if combo:
            out.append(combo)
        combo_f = _keyword_with_suffix(f"{core} {rb[0]}", " 风景")
        if combo_f:
            out.append(combo_f)
    if not out:
        if core:
            out.extend(_daily_search_keyword_matrix([core], daily_ordinal=daily_ordinal, theme_line=core))
        else:
            suf_g = _DAILY_SUFFIX_GROUPS[daily_ordinal % len(_DAILY_SUFFIX_GROUPS)]
            out = [_keyword_with_suffix("旅行", s) for s in suf_g if _keyword_with_suffix("旅行", s)]
    return _dedupe_preserve(out)


def daily_play_keywords(data: dict[str, Any], path: list[Any], slot_label: str) -> list[str]:
    """每日配图槽：与 `daily_enrich_description_keywords` 同一套轮换；无 ``theme`` 时用 ``slot_label`` 推演主干。"""
    ord_ = daily_ordinal_from_path(data, path)
    dd = daily_data_for_path(data, path)
    theme = _normalize_kw(str(dd.get("theme") or "")) if dd else ""
    bases = _theme_search_bases(theme) if theme else []
    spot = _normalize_kw(slot_label)
    out: list[str] = []
    if bases:
        out.extend(_daily_search_keyword_matrix(bases, daily_ordinal=ord_, theme_line=theme))
    elif spot:
        head = _trim_destination(spot) or spot
        fb = _theme_search_bases(head) or ([head] if len(head) >= 2 else [])
        out.extend(_daily_search_keyword_matrix(fb, daily_ordinal=ord_, theme_line=head))

    core = primary_destination_core(data)
    if core and bases:
        rb = _rotate_bases(bases, ord_)
        combo = _keyword_with_suffix(f"{core} {rb[0]}", " 风景")
        if combo:
            out.append(combo)
    elif core and (not out) and spot:
        c = _keyword_with_suffix(core, " 风景")
        if c:
            out.append(c)

    return _dedupe_preserve(out)


def hotel_gallery_keywords(slot_label: str) -> list[str]:
    """住宿 feature 配图：抽出酒店主名 + 环境/房型/区位感。

    slot_label 常为「<酒店主名> <限定词1> <限定词2>」形式。按空格切 token，遇到
    限定词（外观/大堂/客房/房型/早餐/设施/酒店/实拍/民宿/入住/体验）即截断，只保留主名，
    避免「XX酒店 酒店 客房 房型 早餐 酒店 实拍」这种重复堆叠。
    """
    s = _normalize_kw(slot_label)
    if not s:
        return []
    tokens = [t for t in re.split(r"\s+", s) if t]
    name_tokens: list[str] = []
    for t in tokens:
        if t in _HOTEL_QUALIFIERS:
            break
        name_tokens.append(t)
    main = " ".join(name_tokens).strip() or (tokens[0] if tokens else s)
    out = [
        f"{main} 酒店 实拍",
        f"{main} 大堂 外观",
        f"{main} 客房 房型",
        f"{main} 酒店 入住体验",
        f"{main} 舒适",
    ]
    return _dedupe_preserve(out)


def transport_gallery_keywords(slot_label: str) -> list[str]:
    """交通 feature 配图：按 slot_label 识别包车/高铁/航班/自驾分模板，避免「高铁 旅游巴士」错位。"""
    s = _normalize_kw(slot_label)
    if not s:
        return []
    out: list[str] = [f"{s} 实拍", f"{s} 旅行"]
    if any(k in s for k in ("包车", "大巴", "商务车", "MPV", "巴士", "用车")):
        out.append(f"{s} 旅游巴士")
    elif any(k in s for k in ("高铁", "动车", "和谐号", "复兴号", "火车")):
        out.append(f"{s} 站台 实拍")
        out.append("高铁 风景")
    elif any(k in s for k in ("航班", "飞机", "客机", "机场")):
        out.append(f"{s} 客舱 实拍")
        out.append("机场 候机")
    elif any(k in s for k in ("自驾", "公路", "自驾游")):
        out.append(f"{s} 公路 风景")
    else:
        out.append(f"{s} 旅游用车")
    return _dedupe_preserve(out)


def _season_from_check_in(data: dict[str, Any]) -> str:
    """从 tripData.meta.travelCheckIn 推断季节标签（春/夏/秋/冬），无日期返回空串。"""
    meta = data.get("meta") or {}
    check_in = str(meta.get("travelCheckIn") or "").strip()
    if not check_in:
        return ""
    try:
        month = int(check_in.split("-")[1])
    except (ValueError, IndexError):
        return ""
    if 3 <= month <= 5:
        return "春"
    elif 6 <= month <= 8:
        return "夏"
    elif 9 <= month <= 11:
        return "秋"
    else:
        return "冬"


_SEASON_KEYWORDS: dict[str, tuple[str, ...]] = {
    "春": ("赏花", "春色", "花开", "踏青", "花海"),
    "夏": ("避暑", "绿意", "夏日", "清凉", "瀑布"),
    "秋": ("秋色", "红叶", "银杏", "秋高气爽", "金色"),
    "冬": ("雪景", "暖冬", "冰雪", "温泉", "雾凇"),
}

_PHOTO_STYLE_KEYWORDS = (
    "无人机", "航拍", "延时", "广角", "日出", "日落", "星空", "云海",
)


def _keyword_bucket_extensions(kind: str, primary: list[str], data: dict[str, Any], lab: str) -> list[str]:
    """按槽位类型追加「视角/场景/季节/摄影」分桶词，与主检索词交错提高召回多样性（仍受 24 字限制）。"""
    seed = ""
    for p in primary:
        t = _normalize_kw(p)
        if not t:
            continue
        cand = _trim_destination(t)
        if len(cand) >= 2:
            seed = cand[:16]
            break
    if not seed:
        seed = (_trim_destination(_normalize_kw(lab)) or primary_destination_core(data) or "旅行")[:16]

    if kind in ("cover_bg", "section_hero", "feature_section_bg"):
        buckets = ("航拍", "实拍", "夜景")
    elif kind == "daily_activity":
        buckets = ("实拍", "游客", "航拍", "栈道")
    elif kind == "hotel_gallery":
        buckets = ("实拍", "外观", "夜景")
    elif kind == "transport_gallery":
        buckets = ("实拍", "窗外", "全景")
    else:
        buckets = ("实拍", "风景")

    out: list[str] = []
    for bw in buckets:
        k = _keyword_with_suffix(seed, f" {bw}")
        if k:
            out.append(k)

    # 摄影风格词（通用）
    for ps in _PHOTO_STYLE_KEYWORDS:
        k = _keyword_with_suffix(seed, f" {ps}")
        if k:
            out.append(k)

    # 季节词（根据出行日期动态匹配）
    season = _season_from_check_in(data)
    for sk in _SEASON_KEYWORDS.get(season, ()):
        k = _keyword_with_suffix(seed, f" {sk}")
        if k:
            out.append(k)

    return _within_length(_dedupe_preserve(out))


def planned_search_keywords(data: dict[str, Any], path: list[Any], slot_label: str) -> list[str]:
    """返回按优先级排序的检索词列表（先例结构化，再拼接地理兜底）。

    出口会过滤长度 > _MAX_KEYWORD_CHARS 的关键词（小红书 search_feeds 经验值），
    避免 cover.title / theme 等超长拼接导致「关键词越多召回越差」。
    """
    kind = classify_image_slot(data, path)
    lab = slot_label or ""

    if kind in ("cover_bg", "section_hero", "feature_section_bg"):
        primary = hero_landmark_keywords(data, lab)
    elif kind == "daily_activity":
        primary = daily_play_keywords(data, path, lab)
    elif kind == "hotel_gallery":
        primary = hotel_gallery_keywords(lab)
    elif kind == "transport_gallery":
        primary = transport_gallery_keywords(lab)
    else:
        primary = [_normalize_kw(lab)] if _normalize_kw(lab) else []

    bucket = _keyword_bucket_extensions(kind, primary, data, lab)
    primary = _dedupe_preserve(primary + bucket)

    # 兜底 seed：避免使用过长的 slot_label（封面 cover.title 常超 30 字符）
    core = primary_destination_core(data)
    lab_norm = _normalize_kw(lab)
    if lab_norm and len(lab_norm) <= _MAX_KEYWORD_CHARS:
        seed = lab_norm
    elif core:
        seed = core
    else:
        seed = "旅行 风景"
    tail = fallback_geo_variants(seed, data)
    return _within_length(_dedupe_preserve(primary + tail))
