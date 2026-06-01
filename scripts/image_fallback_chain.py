"""小红书配图失败时的降级链：飞猪 FlyAI → 开放网络图库 → placehold.co 浅色可见占位图。

与 ``fill_xhs_images.py`` / ``xhs_image_url_rules.is_remote_https_image_url`` 对齐：产出均为 https。"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from xhs_image_url_rules import is_remote_https_image_url, normalize_xhs_image_url

USER_AGENT = "RoadbookImageFallback/1.0 (skills-travel-planner; +https://example.invalid)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def _dedupe_https(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        u = normalize_xhs_image_url(str(raw or "").strip())
        if not is_remote_https_image_url(u) or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def infer_check_in_out(data: dict[str, Any], trip_path: Path) -> tuple[str, str]:
    """优先 meta.generationDate，其次目录名中的 YYYY-MM-DD；默认「今天 / 明天」。"""
    meta = data.get("meta") or {}
    gd = str(meta.get("generationDate") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", gd):
        d0 = datetime.fromisoformat(gd).date()
        d1 = d0 + timedelta(days=1)
        return d0.isoformat(), d1.isoformat()
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", trip_path.name)
    if m:
        d0 = datetime.fromisoformat(m.group(1)).date()
        d1 = d0 + timedelta(days=1)
        return d0.isoformat(), d1.isoformat()
    today = datetime.now().date()
    return today.isoformat(), (today + timedelta(days=1)).isoformat()


def flyai_city_candidates(data: dict[str, Any], path: list[Any], slot_label: str) -> list[str]:
    """供 search-poi 的 --city-name 轮询（短词优先）。"""
    seen: list[str] = []

    def add(x: str) -> None:
        t = (x or "").strip()
        if len(t) < 2 or t in seen:
            return
        seen.append(t)

    try:
        from xhs_search_keyword_rules import daily_data_for_path, primary_destination_core

        dd = daily_data_for_path(data, path)
        if isinstance(dd, dict):
            theme = str(dd.get("theme") or "")
            for part in re.split(r"[·•]", theme):
                add(part.strip())
        meta = data.get("meta") or {}
        title = str(meta.get("title") or "")
        for part in re.split(r"[·•]", title):
            p = part.strip()
            p = re.sub(r"^[一二三四五六七八九十零〇百千两\d]+\s*天\s*[一二三四五六七八九十零〇百千两\d]+\s*晚\s*", "", p).strip()
            add(p)
        add(primary_destination_core(data))
    except Exception:
        pass

    return seen[:14]


def is_transport_feature_section_bg(data: dict[str, Any], path: list[Any], slot_kind: str) -> bool:
    if slot_kind != "feature_section_bg":
        return False
    try:
        ci = path.index("components")
        comp_idx = path[ci + 1]
        comps = data.get("components")
        if not isinstance(comps, list) or not isinstance(comp_idx, int):
            return False
        comp = comps[comp_idx]
        if not isinstance(comp, dict) or comp.get("type") != "feature":
            return False
        dd = comp.get("data")
        return isinstance(dd, dict) and dd.get("subtype") == "交通"
    except (ValueError, IndexError, TypeError):
        return False


def resolve_transport_item_for_path(data: dict[str, Any], path: list[Any]) -> dict[str, Any] | None:
    """配图槽位于交通 feature 的 ``items[*].images[*]`` 时，解析对应条目。"""
    try:
        if path.count("items") < 1:
            return None
        ii = path.index("items")
        item_idx = path[ii + 1]
        ci = path.index("components")
        comp_idx = path[ci + 1]
        if not isinstance(item_idx, int) or not isinstance(comp_idx, int):
            return None
        comps = data.get("components")
        if not isinstance(comps, list) or not (0 <= comp_idx < len(comps)):
            return None
        comp = comps[comp_idx]
        if not isinstance(comp, dict) or comp.get("type") != "feature":
            return None
        dd = comp.get("data")
        if not isinstance(dd, dict) or dd.get("subtype") != "交通":
            return None
        items = dd.get("items")
        if not isinstance(items, list) or not (0 <= item_idx < len(items)):
            return None
        item = items[item_idx]
        return item if isinstance(item, dict) else None
    except (ValueError, IndexError, TypeError):
        return None


def resolve_accommodation_item_for_path(data: dict[str, Any], path: list[Any]) -> dict[str, Any] | None:
    """配图槽位于住宿 feature 的 ``items[*].images[*]`` 时，解析对应住宿条目。"""
    try:
        if path.count("items") < 1:
            return None
        ii = path.index("items")
        item_idx = path[ii + 1]
        ci = path.index("components")
        comp_idx = path[ci + 1]
        if not isinstance(item_idx, int) or not isinstance(comp_idx, int):
            return None
        comps = data.get("components")
        if not isinstance(comps, list) or not (0 <= comp_idx < len(comps)):
            return None
        comp = comps[comp_idx]
        if not isinstance(comp, dict) or comp.get("type") != "feature":
            return None
        dd = comp.get("data")
        if not isinstance(dd, dict) or dd.get("subtype") != "住宿":
            return None
        items = dd.get("items")
        if not isinstance(items, list) or not (0 <= item_idx < len(items)):
            return None
        item = items[item_idx]
        return item if isinstance(item, dict) else None
    except (ValueError, IndexError, TypeError):
        return None


def flyai_search_poi_inner(city: str, keyword: str, timeout: int) -> dict[str, Any] | None:
    """执行 ``flyai search-poi``，解析成功且有 ``itemList`` 时返回 ``data`` 对象，否则 None。"""
    kw = (keyword or "").strip()[:80]
    city = (city or "").strip()[:32]
    if not kw or not city:
        return None
    try:
        proc = subprocess.run(
            ["flyai", "search-poi", "--city-name", city, "--keyword", kw],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        payload = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None
    inner = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(inner, dict):
        return None
    il = inner.get("itemList")
    if not isinstance(il, list) or len(il) < 1:
        return None
    return inner


def flyai_keyword_search_pic_urls(query: str, timeout: int = 55, *, max_items: int = 16) -> list[str]:
    """``flyai keyword-search``（飞猪网络关键词检索）→ ``info.picUrl`` 列表。"""
    q = re.sub(r"\s+", " ", (query or "").strip())[:96]
    if len(q) < 2:
        return []
    try:
        proc = subprocess.run(
            ["flyai", "keyword-search", "--query", q],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    try:
        payload = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return []
    inner = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(inner, dict):
        return []
    il = inner.get("itemList")
    if not isinstance(il, list):
        return []
    urls: list[str] = []
    for item in il[:max_items]:
        if not isinstance(item, dict):
            continue
        info = item.get("info")
        if not isinstance(info, dict):
            continue
        pic = str(info.get("picUrl") or info.get("pic_url") or "").strip()
        if pic:
            urls.append(pic)
    return _dedupe_https(urls)


def transport_section_bg_flyai_queries(data: dict[str, Any]) -> list[str]:
    """交通 feature 章节顶栏背景：飞猪网络关键词（公路/用车意象，非小红书风光）。"""
    out: list[str] = []

    def add(q: str) -> None:
        t = re.sub(r"\s+", " ", (q or "").strip())
        if len(t) >= 4 and t not in out:
            out.append(t)

    try:
        from xhs_search_keyword_rules import primary_destination_core

        core = primary_destination_core(data)
        if core:
            add(f"{core} 旅游大巴 公路")
            add(f"{core} 包车 旅游用车")
    except Exception:
        pass
    add("旅游大巴 公路 风景")
    add("33座旅游巴士 包车")
    return out[:6]


def flyai_transport_section_bg_urls(
    data: dict[str, Any],
    timeout: int,
    *,
    min_images: int = 4,
) -> list[str]:
    urls: list[str] = []
    for q in transport_section_bg_flyai_queries(data):
        if len(urls) >= min_images:
            break
        urls = _dedupe_https(urls + flyai_keyword_search_pic_urls(q, timeout))
    return urls[: max(min_images, 12)]


def transport_flyai_keyword_queries(
    item_title: str,
    slot_label: str,
    data: dict[str, Any],
) -> list[str]:
    """交通用车图：飞猪关键词检索词（含车型/目的地）。"""
    out: list[str] = []

    def add(q: str) -> None:
        t = re.sub(r"\s+", " ", (q or "").strip())
        if len(t) >= 4 and t not in out:
            out.append(t)

    title = re.sub(r"\s+", " ", (item_title or "").strip())
    label = re.sub(r"\s+", " ", (slot_label or "").strip())
    if title:
        add(title)
        if "巴士" in title or "大巴" in title:
            add(re.sub(r"（[^）]*）", "", title).strip() + " 实拍")
    if label and label not in (title,):
        add(label)
    try:
        from xhs_search_keyword_rules import primary_destination_core

        core = primary_destination_core(data)
        if core:
            add(f"{core} 旅游大巴 包车")
            add(f"{core} 33座 旅游巴士")
    except Exception:
        pass
    add("33座旅游巴士 包车")
    add("旅游大巴 包车 实拍")
    return out[:8]


def flyai_transport_mainpics(
    *,
    item_title: str,
    slot_label: str,
    data: dict[str, Any],
    timeout: int,
    min_images: int = 4,
) -> list[str]:
    """交通 feature 用车图：优先飞猪 keyword-search（网络），不用小红书。"""
    urls: list[str] = []
    for q in transport_flyai_keyword_queries(item_title, slot_label, data):
        if len(urls) >= min_images:
            break
        urls = _dedupe_https(urls + flyai_keyword_search_pic_urls(q, timeout))
    return urls[: max(min_images, 12)]


def flyai_search_poi_mainpics(city: str, keyword: str, timeout: int) -> list[str]:
    kw = (keyword or "").strip()[:80]
    city = (city or "").strip()[:32]
    if not kw or not city:
        return []
    inner = flyai_search_poi_inner(city, kw, timeout)
    if not isinstance(inner, dict):
        return []
    il = inner.get("itemList")
    if not isinstance(il, list):
        return []
    urls: list[str] = []
    for item in il[:12]:
        if not isinstance(item, dict):
            continue
        pic = (item.get("mainPic") or "").strip()
        if pic:
            urls.append(pic)
    return _dedupe_https(urls)


def flyai_hotel_mainpics_from_context(
    acc_item: dict[str, Any],
    slot_label: str,
    check_in: str,
    check_out: str,
    timeout: int,
) -> list[str]:
    try:
        from flyai_hotel_shared import parse_hotel_names, run_flyai
    except ImportError:
        return []

    title = str(acc_item.get("title") or "").strip()
    desc = str(acc_item.get("description") or "")
    city = title
    names = parse_hotel_names(desc)
    kw = names[0] if names else ""
    if not kw:
        kw = re.split(r"\s+", (slot_label or "").replace("酒店", " ").strip())[0][:48]

    inner = run_flyai(city, kw, check_in, check_out, timeout=timeout)
    if not isinstance(inner, dict):
        return []
    il = inner.get("itemList")
    if not isinstance(il, list):
        return []
    urls: list[str] = []
    for h in il[:10]:
        if not isinstance(h, dict):
            continue
        pic = (h.get("mainPic") or "").strip()
        if pic:
            urls.append(pic)
    return _dedupe_https(urls)


def commons_image_urls_for_query(query: str, limit: int, timeout: float = 12.0) -> list[str]:
    """从 Wikimedia Commons 搜索 File，取可外链的 https 原图或缩放图。"""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": q,
        "srnamespace": "6",
        "srlimit": str(min(max(limit * 2, 5), 20)),
    }
    url = COMMONS_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    hits = (((blob or {}).get("query") or {}).get("search")) or []
    if not isinstance(hits, list):
        return []
    titles: list[str] = []
    for h in hits:
        if isinstance(h, dict) and isinstance(h.get("title"), str):
            titles.append(h["title"])
        if len(titles) >= limit + 5:
            break
    if not titles:
        return []

    params2 = {
        "action": "query",
        "format": "json",
        "titles": "|".join(titles[:15]),
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "1600",
    }
    url2 = COMMONS_API + "?" + urllib.parse.urlencode(params2)
    req2 = urllib.request.Request(url2, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req2, timeout=timeout) as resp:
            pages = (((json.loads(resp.read().decode("utf-8", errors="replace")) or {}).get("query") or {}).get(
                "pages"
            )) or {}
    except Exception:
        return []
    urls: list[str] = []
    if not isinstance(pages, dict):
        return []
    for _pid, pg in pages.items():
        if not isinstance(pg, dict):
            continue
        infos = pg.get("imageinfo")
        if not isinstance(infos, list):
            continue
        for ii in infos:
            if not isinstance(ii, dict):
                continue
            thumb = str(ii.get("thumburl") or "").strip()
            full = str(ii.get("url") or "").strip()
            cand = thumb if thumb.startswith("https://") else full
            if cand.startswith("https://"):
                low = cand.lower()
                if any(ext in low for ext in (".djvu", ".pdf", ".djv", ".svg", ".tif", ".tiff")):
                    continue
                urls.append(cand)
            if len(urls) >= limit:
                return _dedupe_https(urls)
    return _dedupe_https(urls)


def placeholder_blank_urls(count: int, seed: str) -> list[str]:
    """降级占位图：使用浅灰底 + 深灰字，避免路书白/ cream 底色上「有 URL 但看起来像没图」。

    （旧版纯白 / 纯白在版面上不可见；仍用 placehold.co + query 满足去重与 https 门禁。）
    """
    sig = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    base = "https://placehold.co/1600x900/e2e8f0/64748b.png"
    return [f"{base}?v={i}&s={sig}" for i in range(max(count, 0))]


def collect_fallback_urls(
    *,
    prefix_urls: list[str],
    data: dict[str, Any],
    path: list[Any],
    slot_kind: str,
    slot_label: str,
    keyword: str,
    trip_path: Path,
    min_images: int,
    max_images: int,
    flyai_timeout: int,
    web_timeout: float = 12.0,
) -> tuple[list[str], list[str]]:
    """返回「尚未出现在 prefix_urls 中的」补足 URL 列表，以及来源标记。

    tags 示例：``flyai_keyword_transport`` / ``flyai_hotel`` / ``flyai_poi:黎平`` / ``commons`` / ``placeholder``。
    """
    pref = _dedupe_https(prefix_urls)
    pref_set = set(pref)
    gap = max(0, min_images - len(pref))
    room = max(0, max_images - len(pref))
    if gap == 0 or room == 0:
        return [], []

    found: list[str] = []
    tags: list[str] = []

    poi_kw = re.sub(r"\s+", " ", (keyword or slot_label or "").strip())[:48]
    if not poi_kw:
        poi_kw = (slot_label or "")[:48]

    def combined() -> list[str]:
        return _dedupe_https(pref + found)

    def still_need() -> bool:
        return len(combined()) < min_images

    def has_room() -> bool:
        return len(combined()) < max_images

    check_in, check_out = infer_check_in_out(data, trip_path)

    seen_new: set[str] = set()

    def absorb(urls: list[str]) -> None:
        for raw in urls:
            if not still_need() or not has_room():
                break
            u = normalize_xhs_image_url(str(raw or "").strip())
            if not is_remote_https_image_url(u):
                continue
            if u in pref_set or u in seen_new:
                continue
            seen_new.add(u)
            found.append(u)

    # 1) 飞猪 · 交通（keyword-search 网络图）
    if slot_kind == "transport_gallery":
        acc = resolve_transport_item_for_path(data, path)
        title = str((acc or {}).get("title") or slot_label or poi_kw)
        hs = flyai_transport_mainpics(
            item_title=title,
            slot_label=slot_label or poi_kw,
            data=data,
            timeout=flyai_timeout,
            min_images=min_images,
        )
        before_n = len(combined())
        absorb(hs)
        if len(combined()) > before_n:
            tags.append("flyai_keyword_transport")
    elif is_transport_feature_section_bg(data, path, slot_kind):
        pics = flyai_transport_section_bg_urls(data, flyai_timeout, min_images=min_images)
        before_n = len(combined())
        absorb(pics)
        if len(combined()) > before_n:
            tags.append("flyai_keyword_transport_bg")

    # 2) 飞猪 · 酒店
    if slot_kind == "hotel_gallery":
        acc = resolve_accommodation_item_for_path(data, path)
        if acc:
            hs = flyai_hotel_mainpics_from_context(acc, slot_label, check_in, check_out, flyai_timeout)
            before_n = len(combined())
            absorb(hs)
            if len(combined()) > before_n:
                tags.append("flyai_hotel")

    # 3) 飞猪 · 景点 POI（交通槽已走 keyword-search 时可跳过重复 POI）
    if still_need() and slot_kind != "transport_gallery" and not is_transport_feature_section_bg(
        data, path, slot_kind
    ):
        cities = flyai_city_candidates(data, path, slot_label)
        for city in cities:
            if not still_need():
                break
            pics = flyai_search_poi_mainpics(city, poi_kw, flyai_timeout)
            before_n = len(combined())
            absorb(pics)
            if len(combined()) > before_n:
                tags.append(f"flyai_poi:{city}")

    # 4) 开放网络（Commons）
    if still_need():
        web_q = re.sub(r"\s+", " ", (keyword or slot_label or "").strip())
        commons = commons_image_urls_for_query(web_q, limit=max(room * 2, gap * 2, 8), timeout=web_timeout)
        before_n = len(combined())
        absorb(commons)
        if len(combined()) > before_n:
            tags.append("commons")

    # 5) 占位（仅补足当前缺口，避免大批量生成彩色占位）
    if still_need():
        seed = "|".join([slot_label, poi_kw, str(path)])
        need_n = max(0, min_images - len(combined()))
        cap_n = max(0, max_images - len(combined()))
        n_gen = min(need_n, cap_n)
        placeholders = placeholder_blank_urls(n_gen, seed)
        before_n = len(combined())
        absorb(placeholders)
        if len(combined()) > before_n:
            tags.append("placeholder")

    final = combined()[:max_images]
    extra = [u for u in final if u not in pref_set]
    return extra, tags
