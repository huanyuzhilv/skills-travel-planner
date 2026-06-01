"""Generate travel plan HTML from tripData JSON and template.

Examples:
  python3 generate.py tripData.json output.html
  python3 generate.py tripData.json output.html --pdf
  python3 generate.py tripData.json output.html --pdf --pdf-path output.pdf
"""

import argparse
import hashlib
import http.server
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _find_chrome_binary() -> Optional[str]:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    for binary in candidates:
        if os.path.exists(binary):
            return binary
    return None


def _fetch_json(url: str) -> Dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; travel-planner/1.0)"
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


IMAGE_FIELD_KEYS = {
    "image",
    "images",
    "imageUrl",
    "imageUrls",
    "backgroundImage",
    "topImages",
    "bottomImages",
    "sideImage",
    "logo",
    "alternates",
    "url",
    "src",
    "href",
}

IMAGE_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/heic": ".jpg",
    "image/heif": ".jpg",
}

POI_IMAGE_SOURCE_PRIORITY = ("xiaohongshu", "wikimedia", "fallback", "manual")
POI_PRIMARY_REGISTRY_PRIORITY = ("xiaohongshu",)
POI_FINAL_REGISTRY_PRIORITY = ("manual",)
HOTEL_IMAGE_SOURCE_PRIORITY = ("ctrip", "fliggy", "xiaohongshu", "manual", "wikimedia", "fallback")
# --image-provider xiaohongshu 时：酒店配图也优先读 registry 里的小红书桶，再携程/飞猪…
HOTEL_IMAGE_SOURCE_PRIORITY_XHS_FIRST = (
    "xiaohongshu",
    "ctrip",
    "fliggy",
    "manual",
    "wikimedia",
    "fallback",
)
HOTEL_INFO_SOURCE_PRIORITY = ("ctrip", "fliggy", "manual")
V2_MIN_CANDIDATE_IMAGES = 6
V2_MAX_CANDIDATE_IMAGES = 10
SOURCE_ALIASES = {
    "xhs": "xiaohongshu",
    "red": "xiaohongshu",
    "小红书": "xiaohongshu",
    "携程": "ctrip",
    "飞猪": "fliggy",
    "人工": "manual",
}


def _is_remote_url(value: str) -> bool:
    return str(value).startswith(("http://", "https://"))


def _is_data_url(value: str) -> bool:
    return str(value).startswith("data:image/")


def _image_entry_slot_label(value) -> str:
    if isinstance(value, dict):
        slot_label = value.get("slotLabel") or value.get("label") or value.get("placeName")
        return str(slot_label).strip() if slot_label else ""
    if isinstance(value, list):
        for item in value:
            label = _image_entry_slot_label(item)
            if label:
                return label
    return ""


def _normalize_source_name(source: str) -> str:
    raw = str(source or "").strip()
    return SOURCE_ALIASES.get(raw, raw.lower())


def _normalize_identifier(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _iter_identifiers(*values: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        variants = [raw, _normalize_identifier(raw)]
        for item in variants:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _coerce_url_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        urls: List[str] = []
        for item in value:
            urls.extend(_coerce_url_list(item))
        return [u for u in urls if u]
    if isinstance(value, dict):
        urls: List[str] = []
        if isinstance(value.get("images"), list):
            urls.extend(_coerce_url_list(value.get("images")))
        if isinstance(value.get("imageUrls"), list):
            urls.extend(_coerce_url_list(value.get("imageUrls")))
        if value.get("imageUrl"):
            urls.extend(_coerce_url_list(value.get("imageUrl")))
        if value.get("url"):
            urls.extend(_coerce_url_list(value.get("url")))
        if isinstance(value.get("alternates"), list):
            urls.extend(_coerce_url_list(value.get("alternates")))
        return [u for u in urls if u]
    return []


def _dedupe_urls(urls: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for url in urls:
        clean = str(url or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _get_nested_value(obj, path: str):
    current = obj
    for part in path.split("."):
        current = current[int(part)] if part.isdigit() else current[part]
    return current


def _set_image_value_preserving_label(obj: Dict, path: str, value) -> None:
    try:
        existing = _get_nested_value(obj, path)
    except Exception:
        existing = None
    urls = _dedupe_urls(_coerce_url_list(value))
    if not urls:
        _set_nested_value(obj, path, "")
        return
    if isinstance(existing, dict):
        slot_label = _image_entry_slot_label(existing)
        if len(urls) > 1:
            payload = {"alternates": urls}
            if slot_label:
                payload["slotLabel"] = slot_label
            _set_nested_value(obj, path, payload)
        else:
            _set_nested_value(obj, path, {"url": urls[0], "slotLabel": slot_label} if slot_label else urls[0])
    elif len(urls) > 1:
        _set_nested_value(obj, path, urls)
    else:
        _set_nested_value(obj, path, urls[0])


def search_commons_images(query: str, limit: int = 3) -> List[str]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": max(limit * 4, 12),
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "iiurlwidth": 1800,
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)

    try:
        data = _fetch_json(url)
    except Exception:
        return []

    pages = data.get("query", {}).get("pages", {})
    candidates: List[tuple] = []
    bad_title_tokens = (".djvu", "scanned", "scan", "book", "journal", "periodical", "cadal")
    for page in pages.values():
        title = str(page.get("title", "")).lower()
        if any(token in title for token in bad_title_tokens):
            continue
        image_infos = page.get("imageinfo", [])
        if not image_infos:
            continue
        info = image_infos[0]
        mime = str(info.get("mime", ""))
        if not mime.startswith("image/"):
            continue
        width = int(info.get("width", 0) or 0)
        height = int(info.get("height", 0) or 0)
        if width < 1000 or height < 700:
            continue
        image_url = info.get("thumburl") or info.get("url")
        if not image_url:
            continue
        # Commons 部分 thumburl 会带 ?utm_* 跟踪参数，本地下载/部分客户端会失败，统一去掉查询串
        image_url = str(image_url).split("?", 1)[0]
        lowered_url = str(image_url).lower()
        if ".djvu" in lowered_url or "page1-" in lowered_url:
            continue
        # Prefer larger images as a rough quality proxy.
        candidates.append((width * height, str(image_url)))

    candidates.sort(key=lambda item: item[0], reverse=True)
    deduped: List[str] = []
    seen = set()
    for _, img_url in candidates:
        if img_url in seen:
            continue
        seen.add(img_url)
        deduped.append(img_url)
        if len(deduped) >= limit:
            break
    return deduped


def _skill_project_root() -> Path:
    """generate.py 位于 assets/，仓库根为其上一级。"""
    return Path(__file__).resolve().parent.parent


def _xhs_extract_image_urls(value) -> List[str]:
    urls: List[str] = []
    image_url_re = re.compile(r"^https?://", re.I)
    if isinstance(value, str):
        if image_url_re.match(value) and not value.lower().endswith((".mp4", ".mov", ".m3u8")):
            urls.append(value)
    elif isinstance(value, dict):
        priority_keys = (
            "url",
            "src",
            "image",
            "imageUrl",
            "image_url",
            "originalUrl",
            "original_url",
            "traceId",
        )
        for key in priority_keys:
            if key in value:
                urls.extend(_xhs_extract_image_urls(value[key]))
        for key, item in value.items():
            if key not in priority_keys:
                urls.extend(_xhs_extract_image_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_xhs_extract_image_urls(item))
    return urls


def search_xiaohongshu_note_images(
    keyword: str,
    *,
    limit: int = 10,
    timeout_ms: int = 180000,
) -> List[str]:
    """通过 TikHub API 搜索小红书笔记并取图。失败返回空列表。"""
    keyword = re.sub(r"\s+", " ", str(keyword or "").strip())
    if not keyword:
        return []
    root = _skill_project_root()
    scripts_dir = root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from repo_dotenv import load_repo_dotenv
        from tikhub_xhs_feeds import search_note_images_by_keyword
        from xhs_image_url_rules import fingerprint_image_url, normalize_xhs_image_url

        load_repo_dotenv(root)
        return search_note_images_by_keyword(
            root,
            keyword,
            limit=limit,
            timeout_ms=timeout_ms,
            normalize_url=normalize_xhs_image_url,
            fingerprint_image_url=fingerprint_image_url,
            throttle_sleep_ms=lambda _ms: None,
        )
    except Exception:
        return []


def _xhs_keyword_variants(
    search_hint: str,
    destination_hint: str,
    *,
    looks_like_hotel: bool,
    is_itinerary_like: bool,
) -> List[str]:
    """构造小红书搜索关键词（实拍 / 打卡 / 攻略等），并按后缀展开以撑满更高的每槽搜索上限。"""
    base = re.sub(r"\s+", " ", str(search_hint or "").strip())
    dest = re.sub(r"\s+", " ", str(destination_hint or "").strip())
    hints: List[str] = []
    if looks_like_hotel:
        for h in (base, dest):
            if h:
                hints.extend(
                    [
                        f"{h} 酒店",
                        f"{h} 酒店 实拍",
                        f"{h} 住宿 推荐",
                        f"{h} 酒店 探店",
                        f"{h} 住宿体验",
                    ]
                )
    else:
        if base:
            if is_itinerary_like:
                hints.extend(
                    [
                        f"{base} 景点 实拍",
                        f"{base} 行程 打卡",
                        f"{base} 旅游 实拍 打卡",
                        f"{base} 旅行记录",
                        f"{base} 一日游",
                        f"{base} 拍照",
                    ]
                )
            if dest:
                hints.extend(
                    [
                        f"{dest} 实拍 打卡",
                        f"{dest} {base} 旅游".strip(),
                    ]
                )
            hints.extend([f"{base} 实拍 打卡", f"{base} 打卡", f"{base} 避雷", f"{base} 小众"])
    seen: set = set()
    out: List[str] = []
    for h in hints:
        h = re.sub(r"\s+", " ", h).strip()
        if h and h not in seen:
            seen.add(h)
            out.append(h)

    # 后缀组合：让更多关键词可被「每槽小红书尝试上限」消费
    common_tails = (
        "",
        " 攻略",
        " vlog",
        " 航拍",
        " 夜景",
        " 日落",
        " 日出",
        " 星空",
        " 云海",
        " 无人机",
        " 延时",
        " 广角",
        " 亲子游",
        " 机位",
        " 出片",
        " 周末去哪玩",
        " 2026",
        " 假期去哪玩",
    )
    # 从目的地中提取首位地名生成区域搜索后缀（适配任意目的地）
    regional_tails: tuple[str, ...] = ()
    dest_first = re.split(r"[·•、，\s]+", dest)[0].strip() if dest else ""
    if len(dest_first) >= 2:
        regional_tails = (f" {dest_first}旅游", f" {dest_first}攻略", f" {dest_first}周边")

    tails = common_tails + regional_tails
    expanded: List[str] = []
    for h in out:
        for t in tails:
            z = re.sub(r"\s+", " ", (h + str(t))).strip()
            if z and z not in seen:
                seen.add(z)
                expanded.append(z)

    merged = out + expanded
    return merged[:400]


def _unsplash_fallback_urls(query: str, limit: int = 1, offset: int = 0, salt: str = "") -> List[str]:
    # 从 query 中提取关键词用于 loremflickr 搜索
    keywords = re.sub(r'[^\w\s]', '', query).strip().split()
    # 取前3个有意义的关键词
    keywords = [w for w in keywords if len(w) > 1][:3]
    if not keywords:
        keywords = ["travel", "landscape"]
    keyword_str = ",".join(keywords)
    encoded_query = urllib.parse.quote(keyword_str)
    # 使用 loremflickr 作为主 fallback，placehold.co 作为后备
    urls: List[str] = []
    for idx in range(limit):
        # loremflickr 支持通过不同尺寸参数产生不同图片
        w = 1600 + (offset + idx) * 10
        h = 900 + (offset + idx) * 5
        primary = f"https://loremflickr.com/{w}/{h}/{encoded_query}"
        urls.append(primary)
    return urls


def _build_bilingual_image_queries(query: str, destination_hint: str = "") -> List[str]:
    """生成更适合图库检索的中英混合关键词。"""
    raw = " ".join([str(destination_hint or ""), str(query or "")]).strip()
    normalized = re.sub(r"\s+", " ", raw)
    # 通用主题词中英映射（不含特定地点，地点由 destination_hint 动态处理）
    aliases = {
        "湿地": "wetland landscape",
        "亲子": "family travel",
        "酒店": "hotel exterior room",
        "餐饮": "local food",
        "美食": "local food",
        "越野": "off road vehicle",
        "摄影": "travel photography landscape",
        "草原": "grassland landscape",
        "骑马": "horse riding",
        "雪山": "snow mountain landscape",
        "海岛": "island beach travel",
        "古镇": "ancient town China",
        "瀑布": "waterfall landscape",
        "峡谷": "canyon landscape",
        "森林": "forest landscape",
        "湖泊": "lake landscape",
        "沙漠": "desert landscape",
        "寺庙": "temple China",
        "梯田": "terrace rice field China",
    }

    translated = normalized
    hits: List[str] = []
    for zh, en in aliases.items():
        if zh in normalized:
            hits.append(en)
            translated = translated.replace(zh, f"{zh} {en}")

    queries = [normalized, translated]
    queries.extend(hits)
    # 从目的地动态构造英文地理查询（适配任意目的地）
    if destination_hint:
        dh_clean = re.sub(r"\s+", " ", destination_hint).strip()
        queries.append(f"{dh_clean} travel landscape")
        queries.append(f"{dh_clean} China tourism")
    queries.append("travel photography landscape UNESCO China")

    out: List[str] = []
    seen = set()
    for item in queries:
        q = str(item or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def _is_unstable_url(url: str) -> bool:
    lowered = str(url).lower()
    if "source.unsplash.com" in lowered:
        return True
    # picsum.photos 服务已不可用（返回 HTTP 405），全部视为不稳定
    if "picsum.photos" in lowered:
        return True
    return False


def _is_reliable_url(url: str) -> bool:
    lowered = str(url).lower()
    return "upload.wikimedia.org/" in lowered


def _activity_image_list(activity: Dict) -> List[str]:
    image_urls = []
    if isinstance(activity.get("imageUrls"), list):
        image_urls.extend([
            str(url).strip()
            for url in activity.get("imageUrls", [])
            if str(url).strip() and not _is_unstable_url(str(url))
        ])
    if activity.get("imageUrl"):
        url = str(activity.get("imageUrl")).strip()
        if url and (not _is_unstable_url(url)) and url not in image_urls:
            image_urls.append(url)
    return image_urls


def _set_activity_image_list(activity: Dict, image_urls: List[str]) -> None:
    deduped = []
    seen = set()
    for url in image_urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    activity["imageUrls"] = deduped
    if deduped:
        activity["imageUrl"] = deduped[0]


def _load_image_registry(registry_path: Optional[str]) -> Dict:
    if not registry_path:
        return {}
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _default_image_registry_path() -> Optional[str]:
    candidate = Path(__file__).resolve().parent / "image_registry.sample.json"
    return str(candidate) if candidate.exists() else None


def _registry_source_bucket(registry: Dict, source: str) -> Dict:
    if not registry:
        return {}
    source = _normalize_source_name(source)
    sources = registry.get("sources", {})
    if isinstance(sources, dict):
        bucket = sources.get(source, {})
        if isinstance(bucket, dict):
            return bucket
    bucket = registry.get(source, {})
    return bucket if isinstance(bucket, dict) else {}


def _lookup_bucket_value(bucket: Dict, entity_type: str, identifiers: List[str]):
    if not isinstance(bucket, dict):
        return None

    candidate_buckets = []
    for key in (entity_type, f"{entity_type}s", "keyword", "keywords", "place", "places"):
        value = bucket.get(key)
        if isinstance(value, dict):
            candidate_buckets.append(value)

    # 兼容 source 下面直接用关键词做 key 的扁平结构。
    candidate_buckets.append(bucket)

    normalized_ids = [_normalize_identifier(item) for item in identifiers]
    for entity_bucket in candidate_buckets:
        for identifier, normalized in zip(identifiers, normalized_ids):
            if identifier in entity_bucket:
                return entity_bucket[identifier]
            if normalized in entity_bucket:
                return entity_bucket[normalized]
        for key, value in entity_bucket.items():
            key_norm = _normalize_identifier(key)
            if any(identifier and (identifier in key or key in identifier) for identifier in identifiers):
                return value
            if any(normalized and (normalized in key_norm or key_norm in normalized) for normalized in normalized_ids):
                return value
    return None


def _lookup_priority_images(
    registry: Dict,
    entity_type: str,
    identifiers: List[str],
    source_priority: Tuple[str, ...],
) -> List[str]:
    if not registry or not identifiers:
        return []
    for source in source_priority:
        source = _normalize_source_name(source)
        if source in {"wikimedia", "fallback"}:
            continue
        bucket = _registry_source_bucket(registry, source)
        value = _lookup_bucket_value(bucket, entity_type, identifiers)
        urls = _coerce_url_list(value)
        if urls:
            return urls
    return []


def _lookup_priority_hotel_info(registry: Dict, identifiers: List[str]) -> Dict:
    if not registry or not identifiers:
        return {}
    for source in HOTEL_INFO_SOURCE_PRIORITY:
        bucket = _registry_source_bucket(registry, source)
        value = _lookup_bucket_value(bucket, "hotel", identifiers)
        if isinstance(value, dict):
            info = {
                key: value[key]
                for key in (
                    "name",
                    "description",
                    "address",
                    "rating",
                    "star",
                    "openedAt",
                    "renovatedAt",
                    "roomCount",
                    "roomSize",
                    "sourceUrl",
                )
                if value.get(key) not in (None, "")
            }
            if info:
                info["source"] = source
                return info
    return {}


def apply_hotel_source_info(trip_data: Dict, image_registry: Optional[Dict]) -> int:
    """统一按携程、飞猪、人工顺序补齐酒店信息。"""
    if not image_registry:
        return 0
    updated = 0
    for hotel in trip_data.get("hotels", []) if isinstance(trip_data.get("hotels"), list) else []:
        identifiers = _iter_identifiers(hotel.get("hotelId", ""), hotel.get("name", ""))
        info = _lookup_priority_hotel_info(image_registry, identifiers)
        if not info:
            continue
        for key, value in info.items():
            if key == "source":
                hotel["infoSource"] = value
            elif not hotel.get(key):
                hotel[key] = value
        updated += 1
    return updated


def _lookup_registry_images(registry: Dict, entity_type: str, entity_id: str) -> List[str]:
    if not registry or not entity_id:
        return []
    entity_bucket = registry.get(entity_type, {})
    if not isinstance(entity_bucket, dict):
        return []
    urls = entity_bucket.get(entity_id, [])
    if not isinstance(urls, list):
        return []
    return [str(url).strip() for url in urls if str(url).strip()]


def enrich_images(
    trip_data: Dict,
    min_images: int = 3,
    ensure_day_images: bool = False,
    min_images_per_activity: int = 1,
    strict_images: bool = False,
    image_registry: Optional[Dict] = None,
    image_provider: str = "xiaohongshu",
    image_fallback: str = "none",
    image_timeout_ms: int = 180000,
) -> int:
    assigned = 0
    queries_tried = 0
    max_queries = 40

    activities = []
    day_targets: Dict[int, List[Dict]] = {}
    skip_words = ("出发", "入住", "寄存", "前往", "返", "回酒店", "午餐", "晚餐", "早餐", "简餐", "步行街")
    for day_idx, day in enumerate(trip_data.get("days", [])):
        for activity in day.get("activities", []):
            if activity.get("meal"):
                continue
            poi_id = str(activity.get("poiId", "")).strip()
            name = str(activity.get("name", "")).strip()
            identifiers = _iter_identifiers(poi_id, name)
            registry_urls = _lookup_priority_images(
                image_registry or {},
                "poi",
                identifiers,
                POI_PRIMARY_REGISTRY_PRIORITY,
            )
            if registry_urls:
                current_urls = _activity_image_list(activity)
                _set_activity_image_list(activity, current_urls + registry_urls)
            if not name:
                continue
            if any(word in name for word in skip_words):
                continue
            activities.append(activity)
            day_targets.setdefault(day_idx, []).append(activity)

    hotels = []
    for hotel in trip_data.get("hotels", []):
        hotel_id = str(hotel.get("hotelId", "")).strip()
        hotel_name = str(hotel.get("name", "")).strip()
        identifiers = _iter_identifiers(hotel_id, hotel_name)
        hotel_src = (
            HOTEL_IMAGE_SOURCE_PRIORITY_XHS_FIRST
            if _normalize_source_name(image_provider) == "xiaohongshu"
            else HOTEL_IMAGE_SOURCE_PRIORITY
        )
        registry_urls = _lookup_priority_images(
            image_registry or {},
            "hotel",
            identifiers,
            hotel_src,
        ) or _lookup_registry_images(image_registry or {}, "hotel", hotel_id)
        if registry_urls:
            hotel["imageUrl"] = registry_urls[0]
        if hotel.get("imageUrl") and (not _is_unstable_url(str(hotel.get("imageUrl")))):
            continue
        if hotel_name:
            hotels.append(hotel)

    required_for_activities = len(activities) * max(min_images_per_activity, 1)
    effective_min_images = max(min_images, required_for_activities)

    # 先确保“每天至少一张”，再做总量补齐。
    targets: List[tuple] = []
    if ensure_day_images:
        for day_idx in range(len(trip_data.get("days", []))):
            items = day_targets.get(day_idx, [])
            if items:
                targets.append(("activity", items[0]))

    # 景点优先，其次酒店；避免重复目标。
    seen_ids = {id(item) for _, item in targets}
    for item in activities:
        if id(item) not in seen_ids:
            targets.append(("activity", item))
            seen_ids.add(id(item))
    for item in hotels:
        if id(item) not in seen_ids:
            targets.append(("hotel", item))
            seen_ids.add(id(item))

    for target_type, item in targets:
        if assigned >= effective_min_images:
            break
        if queries_tried >= max_queries:
            break

        base_name = str(item.get("name", "")).strip()
        current_urls = _activity_image_list(item) if target_type == "activity" else []
        need_count = max(min_images_per_activity - len(current_urls), 0) if target_type == "activity" else 1
        if need_count <= 0:
            continue
        query_candidates = [base_name]
        if target_type == "activity":
            query_candidates.extend([f"{base_name} 景点", f"{base_name} landmark"])
        else:
            query_candidates.extend([f"{base_name} 酒店", f"{base_name} hotel exterior"])

        chosen_urls: List[str] = []
        provider = _normalize_source_name(image_provider)
        fallback = str(image_fallback or "none").lower()

        if provider == "xiaohongshu":
            destination_title = str(trip_data.get("title", "")).strip()
            xhs_keys: List[str] = []
            for qc in query_candidates:
                xhs_keys.extend(
                    _xhs_keyword_variants(
                        qc,
                        destination_title,
                        looks_like_hotel=(target_type == "hotel"),
                        is_itinerary_like=True,
                    )
                )
            seen_kw: set = set()
            for kw in xhs_keys:
                if queries_tried >= max_queries or len(chosen_urls) >= need_count:
                    break
                kw = kw.strip()
                if not kw or kw in seen_kw:
                    continue
                seen_kw.add(kw)
                queries_tried += 1
                batch = search_xiaohongshu_note_images(
                    kw, limit=need_count + 4, timeout_ms=image_timeout_ms
                )
                for url in batch:
                    if url in chosen_urls or (target_type == "activity" and url in current_urls):
                        continue
                    chosen_urls.append(url)
                    if len(chosen_urls) >= need_count:
                        break

        if len(chosen_urls) < need_count and (provider == "wikimedia" or fallback in ("wikimedia", "full")):
            for query in query_candidates:
                if queries_tried >= max_queries or len(chosen_urls) >= need_count:
                    break
                queries_tried += 1
                image_urls = search_commons_images(query, limit=need_count + 2)
                if image_urls:
                    for url in image_urls:
                        if url in chosen_urls or (target_type == "activity" and url in current_urls):
                            continue
                        chosen_urls.append(url)
                        if len(chosen_urls) >= need_count:
                            break

        if chosen_urls:
            if target_type == "activity":
                _set_activity_image_list(item, current_urls + chosen_urls)
            else:
                item["imageUrl"] = chosen_urls[0]
            assigned += len(chosen_urls)

    if assigned < effective_min_images and (not strict_images) and str(image_fallback or "").lower() in (
        "wikimedia",
        "full",
    ):
        destination_hint = str(trip_data.get("title", "travel destination")).strip() or "travel destination"
        fb = str(image_fallback or "none").lower()
        for target_type, item in targets:
            if assigned >= effective_min_images:
                break
            if queries_tried >= max_queries:
                break
            base_name = str(item.get("name", "")).strip()
            if target_type == "activity":
                current_urls = _activity_image_list(item)
                need_count = max(min_images_per_activity - len(current_urls), 0)
            else:
                current_urls = []
                need_count = 0 if item.get("imageUrl") else 1
            if need_count <= 0:
                continue

            # 二次 Wikimedia 搜索：使用目的地 + landscape 进行更宽泛搜索
            broader_query = f"{destination_hint} landscape"
            queries_tried += 1
            broader_urls = search_commons_images(broader_query, limit=need_count + 2)
            if broader_urls:
                chosen_urls = [u for u in broader_urls if u not in current_urls][:need_count]
                if chosen_urls:
                    if target_type == "activity":
                        _set_activity_image_list(item, current_urls + chosen_urls)
                    else:
                        item["imageUrl"] = chosen_urls[0]
                    assigned += len(chosen_urls)
                    continue

            # 最终 fallback：使用 loremflickr 占位图（仅 image_fallback=full）
            if fb == "full":
                fallback_query = f"{destination_hint} {base_name} {'landmark' if target_type == 'activity' else 'hotel'}"
                fallback_urls = _unsplash_fallback_urls(fallback_query, limit=need_count, offset=len(current_urls))
                if fallback_urls and target_type == "activity":
                    _set_activity_image_list(item, current_urls + fallback_urls)
                    assigned += len(fallback_urls)
                    continue
                if fallback_urls:
                    item["imageUrl"] = fallback_urls[0]
                    assigned += len(fallback_urls)
                    continue

            if target_type == "activity":
                manual_urls = _lookup_priority_images(
                    image_registry or {},
                    "poi",
                    _iter_identifiers(base_name, destination_hint),
                    POI_FINAL_REGISTRY_PRIORITY,
                )
                manual_urls = [u for u in manual_urls if u not in current_urls][:need_count]
                if manual_urls:
                    _set_activity_image_list(item, current_urls + manual_urls)
                    assigned += len(manual_urls)

    # Always replace unstable hotel links (only in non-strict mode).
    if not strict_images and str(image_fallback or "").lower() == "full":
        for hotel in trip_data.get("hotels", []):
            current = str(hotel.get("imageUrl", "")).strip()
            if current and (not _is_unstable_url(current)):
                continue
            hotel_name = str(hotel.get("name", "")).strip() or "hotel"
            hotel["imageUrl"] = _unsplash_fallback_urls(f"{hotel_name} hotel", limit=1, offset=2)[0]
    else:
        # Strict mode: keep only reliable URLs,宁缺毋滥.
        for day in trip_data.get("days", []):
            for activity in day.get("activities", []):
                urls = [u for u in _activity_image_list(activity) if _is_reliable_url(u)]
                if urls:
                    _set_activity_image_list(activity, urls)
                else:
                    activity.pop("imageUrls", None)
                    activity.pop("imageUrl", None)
        for hotel in trip_data.get("hotels", []):
            url = str(hotel.get("imageUrl", "")).strip()
            if not url or (not _is_reliable_url(url)):
                hotel.pop("imageUrl", None)

    return assigned


def _is_placeholder_url(url) -> bool:
    """检查 URL 是否为空或占位符。"""
    if not url:
        return True
    if isinstance(url, dict):
        alternates = url.get("alternates")
        if isinstance(alternates, list) and alternates:
            return all(_is_placeholder_url(item) for item in alternates)
        return _is_placeholder_url(url.get("url") or url.get("src") or url.get("href") or "")
    if isinstance(url, list):
        return not url or all(_is_placeholder_url(item) for item in url)
    lowered = str(url).strip().lower()
    if not lowered:
        return True
    if "placehold.co" in lowered or "placeholder" in lowered or "loremflickr.com" in lowered:
        return True
    return False


def _collect_image_fields_v2(trip_data: Dict, refresh_all: bool = False) -> List[tuple]:
    """收集 v2 数据中所有需要填充图片的字段路径和关联的地点名称。

    Returns:
        List of (json_path, search_hint) tuples for empty/placeholder image fields.
    """
    fields: List[tuple] = []

    # 封面
    cover = trip_data.get("cover", {})
    if refresh_all or _is_placeholder_url(cover.get("backgroundImage")):
        title = cover.get("title", "") or trip_data.get("meta", {}).get("title", "")
        slot_label = _image_entry_slot_label(cover.get("backgroundImage"))
        fields.append(("cover.backgroundImage", " ".join([slot_label, title]).strip()))

    # 遍历组件
    for i, comp in enumerate(trip_data.get("components", [])):
        comp_type = comp.get("type")
        data = comp.get("data", {})
        base_path = f"components.{i}.data"

        # 通用背景图
        if refresh_all or _is_placeholder_url(data.get("backgroundImage")):
            slot_label = _image_entry_slot_label(data.get("backgroundImage"))
            hint = " ".join([slot_label, data.get("title", "") or data.get("theme", "")]).strip()
            fields.append((f"{base_path}.backgroundImage", hint))

        if comp_type == "daily":
            theme = data.get("theme", "") or data.get("title", "")
            for j, img in enumerate(data.get("topImages", [])):
                if refresh_all or _is_placeholder_url(img):
                    slot_label = _image_entry_slot_label(img)
                    fields.append((f"{base_path}.topImages.{j}", " ".join([slot_label, theme]).strip()))
            if refresh_all or _is_placeholder_url(data.get("sideImage")):
                slot_label = _image_entry_slot_label(data.get("sideImage"))
                fields.append((f"{base_path}.sideImage", " ".join([slot_label, theme]).strip()))
            for j, img in enumerate(data.get("bottomImages", [])):
                if refresh_all or _is_placeholder_url(img):
                    slot_label = _image_entry_slot_label(img)
                    fields.append((f"{base_path}.bottomImages.{j}", " ".join([slot_label, theme]).strip()))

        elif comp_type == "feature":
            for j, item in enumerate(data.get("items", [])):
                images = item.get("images", [])
                if not images:
                    # 空数组时，预填充2个空槽位，让搜图逻辑能填充
                    item["images"] = ["", ""]
                    images = item["images"]
                for k, img in enumerate(images):
                    if refresh_all or _is_placeholder_url(img):
                        # 优先用图片槽命名，其次用 item 标题。
                        slot_label = _image_entry_slot_label(img)
                        search_term = item.get("title", "") or data.get("title", "")
                        fields.append((f"{base_path}.items.{j}.images.{k}", " ".join([slot_label, search_term]).strip()))

    return fields


def _set_nested_value(obj: Dict, path: str, value) -> None:
    """根据点分隔路径设置嵌套字典/列表的值。"""
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if part.isdigit():
            current = current[int(part)]
        else:
            current = current[part]
    last = parts[-1]
    if last.isdigit():
        current[int(last)] = value
    else:
        current[last] = value


def enrich_images_v2(
    trip_data: Dict,
    strict_images: bool = False,
    image_registry: Optional[Dict] = None,
    max_queries: int = 220,
    commons_max_queries: Optional[int] = None,
    refresh_all: bool = False,
    image_provider: str = "xiaohongshu",
    image_fallback: str = "none",
    image_timeout_ms: int = 240000,
) -> int:
    """为 v2 数据结构填充图片 URL。

    默认 ``image_provider=xiaohongshu``；``image_fallback`` 默认 ``none``（不自动维基，仅小红书；需补洞时显式 ``wikimedia`` / ``full``）。
    ``max_queries`` 为**每个图片槽位**的小红书尝试上限；``commons_max_queries`` 为维基兜底上限（仅当 ``image_fallback`` 含维基时）。
    """
    fields = _collect_image_fields_v2(trip_data, refresh_all=refresh_all)
    if not fields:
        return 0

    assigned = 0
    used_urls = set()
    destination_hint = (
        trip_data.get("meta", {}).get("title", "")
        or trip_data.get("cover", {}).get("title", "travel")
    )
    provider = _normalize_source_name(image_provider)
    fb = str(image_fallback or "none").lower()
    xhs_budget = max(8, int(max_queries))
    if commons_max_queries is None:
        # 目标：主源多试、兜底少试（约 1/8～1/10 量级的维基 API 尝试），对齐「九成来自小红书」
        commons_budget = max(14, min(40, max(24, xhs_budget // 9)))
    else:
        commons_budget = max(8, int(commons_max_queries))

    for json_path, hint in fields:
        search_hint = hint or destination_hint

        def choose_candidates(urls: List[str], limit: int = V2_MAX_CANDIDATE_IMAGES) -> List[str]:
            candidates = _dedupe_urls(_coerce_url_list(urls))
            pad_unsplash = fb == "full" and (not strict_images)
            if len(candidates) < V2_MIN_CANDIDATE_IMAGES and pad_unsplash:
                fallback_urls = _unsplash_fallback_urls(
                    f"{destination_hint} {search_hint}",
                    limit=limit - len(candidates),
                    offset=assigned + len(candidates),
                    salt=f"{json_path}:candidates",
                )
                candidates = _dedupe_urls(candidates + fallback_urls)
            if not candidates:
                return []
            chosen = next((u for u in candidates if u not in used_urls), candidates[0])
            ordered = [chosen] + [u for u in candidates if u != chosen]
            return ordered[:limit]

        # 行程类组件优先使用“景点/行程”关键词，降低无关图命中
        is_itinerary_like = (
            ".components." in json_path
            and (
                ".data.items." in json_path
                or ".data.topImages." in json_path
                or ".data.sideImage" in json_path
                or ".data.bottomImages." in json_path
                or ".data.backgroundImage" in json_path
            )
        )
        query_candidates: List[str] = []
        looks_like_hotel = any(token in search_hint for token in ("酒店", "民宿", "住宿", "蒙古包", "套娃"))
        if looks_like_hotel:
            reg_priority = (
                HOTEL_IMAGE_SOURCE_PRIORITY_XHS_FIRST
                if provider == "xiaohongshu"
                else HOTEL_IMAGE_SOURCE_PRIORITY
            )
        else:
            reg_priority = POI_PRIMARY_REGISTRY_PRIORITY
        registry_urls = _lookup_priority_images(
            image_registry or {},
            "hotel" if looks_like_hotel else "poi",
            _iter_identifiers(search_hint, destination_hint),
            reg_priority,
        )
        if registry_urls:
            candidates = choose_candidates(registry_urls)
            _set_image_value_preserving_label(trip_data, json_path, candidates)
            used_urls.add(candidates[0])
            assigned += 1
            continue

        if is_itinerary_like:
            query_candidates.extend([
                f"{search_hint} 景点",
                f"{search_hint} 行程",
                f"{search_hint} 旅游",
                f"{destination_hint} 景点",
            ])
        for candidate in _build_bilingual_image_queries(search_hint, destination_hint):
            query_candidates.append(candidate)
            query_candidates.append(f"{candidate} travel photo")
        query_candidates.extend([search_hint, f"{destination_hint} landscape"])

        urls: List[str] = []
        if provider == "xiaohongshu":
            tried_xhs = 0
            seen_kw: set = set()
            for kw in _xhs_keyword_variants(
                search_hint,
                destination_hint,
                looks_like_hotel=looks_like_hotel,
                is_itinerary_like=is_itinerary_like,
            ):
                if tried_xhs >= xhs_budget:
                    break
                kw = kw.strip()
                if not kw or kw in seen_kw:
                    continue
                seen_kw.add(kw)
                tried_xhs += 1
                urls = search_xiaohongshu_note_images(
                    kw, limit=V2_MAX_CANDIDATE_IMAGES, timeout_ms=image_timeout_ms
                )
                if urls:
                    break

        if not urls and (provider == "wikimedia" or fb in ("wikimedia", "full")):
            tried_commons = 0
            # 仅用维基时提高单槽维基预算（否则双源场景下维基额度偏低）
            wikimedia_budget = max(xhs_budget, commons_budget) if provider == "wikimedia" else commons_budget
            for query in query_candidates:
                if tried_commons >= wikimedia_budget:
                    break
                q = str(query or "").strip()
                if not q:
                    continue
                tried_commons += 1
                urls = search_commons_images(q, limit=V2_MAX_CANDIDATE_IMAGES)
                if urls:
                    break

        if urls:
            candidates = choose_candidates(urls)
            _set_image_value_preserving_label(trip_data, json_path, candidates)
            used_urls.add(candidates[0])
            assigned += 1
            continue

        if (not strict_images) and fb == "full":
            fallback_urls = _unsplash_fallback_urls(
                f"{destination_hint} {search_hint}",
                limit=V2_MAX_CANDIDATE_IMAGES,
                offset=assigned,
                salt=json_path,
            )
            if fallback_urls:
                candidates = choose_candidates(fallback_urls)
                _set_image_value_preserving_label(trip_data, json_path, candidates)
                used_urls.add(candidates[0])
                assigned += 1
                continue

        if not looks_like_hotel:
            manual_urls = _lookup_priority_images(
                image_registry or {},
                "poi",
                _iter_identifiers(search_hint, destination_hint),
                POI_FINAL_REGISTRY_PRIORITY,
            )
            if manual_urls:
                candidates = choose_candidates(manual_urls)
                _set_image_value_preserving_label(trip_data, json_path, candidates)
                used_urls.add(candidates[0])
                assigned += 1

    return assigned


def _guess_image_ext(url: str, content_type: str) -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type in IMAGE_MIME_EXT:
        return IMAGE_MIME_EXT[content_type]
    parsed = urllib.parse.urlparse(url)
    ext = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}:
        return ".jpg" if ext == ".jpeg" else ext
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"} else ".jpg"


def _sniff_image_content_type(data: bytes) -> Optional[str]:
    """对 ``application/octet-stream`` 等响应做魔数识别（常见 CDN 图无扩展名）。"""
    if len(data) < 12:
        return None
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"hevc", b"avif"):
        return "image/heic"
    return None


def _convert_heic_to_jpeg_bytes(data: bytes) -> Optional[bytes]:
    """小红书 CDN 常返回 HEIC；浏览器无法显示 .jpg 扩展名下的 HEIC 字节。"""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.heic"
        out = Path(td) / "out.jpg"
        inp.write_bytes(data)
        try:
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(inp), "--out", str(out)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            if out.exists() and out.stat().st_size > 1024:
                return out.read_bytes()
        except Exception:
            pass
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(inp), str(out)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            if out.exists() and out.stat().st_size > 1024:
                return out.read_bytes()
        except Exception:
            pass
    return None


def _normalize_image_bytes(data: bytes, content_type: str) -> Tuple[bytes, str]:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("image/heic", "image/heif") or _sniff_image_content_type(data) == "image/heic":
        converted = _convert_heic_to_jpeg_bytes(data)
        if converted:
            return converted, "image/jpeg"
    return data, ct if ct.startswith("image/") else "image/jpeg"


def _download_image_to_local(url: str, assets_dir: Path, rel_prefix: str, cache: Dict[str, str]) -> Optional[str]:
    if url in cache:
        return cache[url]
    if not _is_remote_url(url) or _is_data_url(url):
        return url

    # 命中已落地的本地文件直接复用，避免重复发起 HTTP 请求
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    for cand_ext in (".jpg", ".png", ".webp", ".jpeg", ".gif", ".svg"):
        existing = assets_dir / f"img-{digest}{cand_ext}"
        if existing.exists() and existing.stat().st_size > 1024:
            rel = f"{rel_prefix}/{existing.name}".replace("\\", "/")
            cache[url] = rel
            return rel

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) travel-planner/1.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            header_ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read()
    except Exception:
        return None

    if not data:
        return None
    content_type = header_ct
    if not content_type.startswith("image/"):
        sniffed = _sniff_image_content_type(data)
        if sniffed:
            content_type = sniffed
        else:
            return None
    data, content_type = _normalize_image_bytes(data, content_type)
    ext = _guess_image_ext(url, content_type)
    filename = f"img-{digest}{ext}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    target = assets_dir / filename
    if not target.exists():
        target.write_bytes(data)
    rel = f"{rel_prefix}/{filename}".replace("\\", "/")
    cache[url] = rel
    return rel


def _localize_image_node(node, assets_dir: Path, rel_prefix: str, cache: Dict[str, str], in_image_context: bool = False) -> Tuple[object, int, int]:
    if isinstance(node, dict):
        changed = 0
        failed = 0
        out = {}
        for key, value in node.items():
            child_context = in_image_context or key in IMAGE_FIELD_KEYS
            new_value, c, f = _localize_image_node(value, assets_dir, rel_prefix, cache, child_context)
            out[key] = new_value
            changed += c
            failed += f
        return out, changed, failed

    if isinstance(node, list):
        changed = 0
        failed = 0
        out = []
        for value in node:
            new_value, c, f = _localize_image_node(value, assets_dir, rel_prefix, cache, in_image_context)
            out.append(new_value)
            changed += c
            failed += f
        return out, changed, failed

    if isinstance(node, str) and in_image_context and _is_remote_url(node):
        localized = _download_image_to_local(node, assets_dir, rel_prefix, cache)
        if localized:
            return localized, 1 if localized != node else 0, 0
        return node, 0, 1

    return node, 0, 0


def _collect_remote_image_urls(node, urls: set, in_image_context: bool = False) -> None:
    """收集 trip_data 中所有处于图片上下文里的远程 URL（含 alternates）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            _collect_remote_image_urls(value, urls, in_image_context or key in IMAGE_FIELD_KEYS)
    elif isinstance(node, list):
        for value in node:
            _collect_remote_image_urls(value, urls, in_image_context)
    elif isinstance(node, str) and in_image_context and _is_remote_url(node) and not _is_data_url(node):
        urls.add(node)


def localize_trip_images(
    trip_data: Dict,
    output_html: str,
    assets_dir_name: str = "roadbook-images",
    workers: int = 16,
) -> Tuple[int, int]:
    """下载远程图片到 HTML 同级目录，避免预览/导出时依赖不稳定外链。

    使用线程池并发下载（仅 I/O 等待）。下载完成后用 URL→本地路径的映射做整体替换。
    """
    output_path = Path(output_html).resolve()
    assets_dir = output_path.parent / assets_dir_name

    # 1) 收集所有待下载 URL（去重）
    urls: set = set()
    _collect_remote_image_urls(trip_data, urls)
    if not urls:
        return 0, 0

    # 2) 并发下载，构造 URL → 本地相对路径 的映射
    cache: Dict[str, str] = {}
    failed_count = 0
    rel_prefix = assets_dir.name
    workers = max(1, min(workers, 32))

    def _worker(u: str) -> Tuple[str, Optional[str]]:
        return u, _download_image_to_local(u, assets_dir, rel_prefix, {})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, u) for u in urls]
        for fut in as_completed(futures):
            u, local = fut.result()
            if local:
                cache[u] = local
            else:
                failed_count += 1

    # 3) 用映射做一次完整替换（_localize_image_node 在 cache 命中时直接返回，不再发请求）
    localized, changed, _ = _localize_image_node(
        trip_data,
        assets_dir=assets_dir,
        rel_prefix=rel_prefix,
        cache=cache,
        in_image_context=False,
    )
    trip_data.clear()
    trip_data.update(localized)
    return changed, failed_count


# 内置模板别名：可通过 --template 选择（路径相对于 assets/ 目录）
TEMPLATE_ALIASES = {
    "default": os.path.join("templates", "default", "template.html"),
    "brochure": os.path.join("templates", "brochure", "template-brochure.html"),
    "roadbook": os.path.join("templates", "roadbook", "template-roadbook.html"),
    "roadbook-v2": os.path.join("templates", "roadbook-v2", "template-roadbook-v2.html"),
}

# 旧版 assets 根目录下的模板文件名，仍可作为参数传入
LEGACY_TEMPLATE_FILENAMES = {
    "template.html": TEMPLATE_ALIASES["default"],
    "template-brochure.html": TEMPLATE_ALIASES["brochure"],
    "template-roadbook.html": TEMPLATE_ALIASES["roadbook"],
    "template-roadbook-v2.html": TEMPLATE_ALIASES["roadbook-v2"],
}


def _resolve_template_path(template: Optional[str]) -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not template:
        return os.path.join(script_dir, TEMPLATE_ALIASES["default"])
    # 支持别名
    if template in TEMPLATE_ALIASES:
        return os.path.join(script_dir, TEMPLATE_ALIASES[template])
    # 支持绝对/相对路径
    if os.path.isabs(template) and os.path.exists(template):
        return template
    candidate = os.path.join(script_dir, template)
    if os.path.exists(candidate):
        return candidate
    legacy_rel = LEGACY_TEMPLATE_FILENAMES.get(os.path.basename(template))
    if legacy_rel:
        legacy_path = os.path.join(script_dir, legacy_rel)
        if os.path.exists(legacy_path):
            return legacy_path
    return template


def _is_roadbook_template(template_name: Optional[str]) -> bool:
    """Check if the resolved template is the roadbook (v1) template."""
    if template_name == "roadbook":
        return True
    if template_name and "template-roadbook" in template_name:
        # Exclude v2 variant
        if "roadbook-v2" in template_name:
            return False
        return True
    return False


def _is_roadbook_v2_template(template_name: Optional[str]) -> bool:
    """检测是否使用 v2 路书模板"""
    if template_name == "roadbook-v2":
        return True
    if template_name and "template-roadbook-v2" in template_name:
        return True
    return False


def _auto_detect_template(trip_data: Dict) -> Optional[str]:
    """Auto-detect template based on tripData content.

    Priority: v2.0 > roadbook v1 > None (default).
    """
    # v2.0 检测：有 meta.version == "2.0" 且有 components 数组
    meta = trip_data.get("meta", {})
    if meta.get("version") == "2.0" and "components" in trip_data:
        return "roadbook-v2"

    # 原有的 v1 roadbook 检测逻辑保留
    if "vehicles" in trip_data or "foods" in trip_data:
        return "roadbook"
    costs = trip_data.get("costs")
    if isinstance(costs, dict) and "included" in costs:
        return "roadbook"
    return None


def _apply_v2_tripdata_export_prep(trip_data: Dict, *, log: bool = True) -> None:
    """v2：出行须知按需归并；仅有费用块时补一页空白「服务说明」（无简表默认长文，便于手填）。"""
    meta = trip_data.get("meta") or {}
    if meta.get("version") != "2.0" or "components" not in trip_data:
        return
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if not scripts_dir.is_dir():
        return
    sd = str(scripts_dir)
    if sd not in sys.path:
        sys.path.insert(0, sd)
    try:
        from roadbook_intake import (  # noqa: PLC0415
            align_travel_notice_with_service_text_block,
            ensure_default_service_text_block,
            ensure_hotel_feature_module,
        )

        if align_travel_notice_with_service_text_block(trip_data) and log:
            print(
                "  [roadbook-v2] 出行须知：已按输入处理（无正文则移除，有正文则并入服务说明）",
                flush=True,
            )
        if ensure_default_service_text_block(trip_data) and log:
            print(
                "  [roadbook-v2] tripData 仅有费用说明：已在导出 HTML 前插入空白「服务说明」页（可手填）",
                flush=True,
            )
        if ensure_hotel_feature_module(trip_data) and log:
            print(
                "  [roadbook-v2] 已补全空白「住宿安排」模块（无酒店信息或未匹配时可手填）",
                flush=True,
            )
    except Exception as exc:
        if log:
            print(f"  WARNING: 未能应用 v2 文字块归一化（{exc}）", flush=True)


def generate_html(
    data_path: str,
    output_path: str,
    template: Optional[str] = None,
    *,
    print_layout_hints: bool = True,
    content_length_warnings: bool = True,
):
    with open(data_path, "r", encoding="utf-8") as f:
        trip_data = json.load(f)

    _apply_v2_tripdata_export_prep(trip_data, log=True)

    # Auto-detect roadbook template if no template specified
    effective_template = template
    if not effective_template:
        detected = _auto_detect_template(trip_data)
        if detected:
            effective_template = detected
            print(f"Auto-detected template: {effective_template}")

    if content_length_warnings and _is_roadbook_v2_template(effective_template):
        try:
            from tripdata_content_limits import warn_oversized_fields  # noqa: PLC0415

            nwarn = warn_oversized_fields(trip_data, label=os.path.basename(data_path))
            if nwarn:
                print(
                    f"  [roadbook-v2] 文案长度提示：{nwarn} 条（见上）；导出仍可继续。",
                    flush=True,
                )
        except Exception as exc:
            print(f"  WARNING: 文案长度检查失败（{exc}）", flush=True)

    if print_layout_hints and _is_roadbook_v2_template(effective_template):
        try:
            from roadbook_print_layout import apply_print_layout_hints  # noqa: PLC0415

            if apply_print_layout_hints(trip_data):
                print(
                    "  [roadbook-v2] 已写入印刷预分页提示 meta.printLayout.breakBeforeIndices",
                    flush=True,
                )
        except Exception as exc:
            print(f"  WARNING: 印刷预分页提示失败（{exc}）", flush=True)

    template_path = _resolve_template_path(effective_template)

    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    if _is_roadbook_v2_template(effective_template) or _is_roadbook_template(effective_template):
        # 路书模板（v1 & v2）：TRIP_DATA 仅替换为 JSON 字面量；v2 模板外层已有 /*TRIPDATA_START*/…END 包裹，
        # 避免在 const 右侧再嵌套一对标记（会导致预览服务正则误匹配、脚本损坏）。
        trip_data_json = json.dumps(trip_data, ensure_ascii=False, indent=2)
        html = html.replace("TRIP_DATA", trip_data_json, 1)
    else:
        # 标准/宣传册模板：逐字段替换占位符
        duration = trip_data.get("duration", "")
        if not duration and isinstance(trip_data.get("days"), list):
            day_count = len(trip_data["days"])
            if day_count > 0:
                duration = f"{day_count}天{max(day_count - 1, 0)}晚"

        replacements = {
            "{{TRIP_DATA_JSON}}": json.dumps(trip_data, ensure_ascii=False, indent=2),
            "{{TRIP_TITLE}}": trip_data.get("title", ""),
            "{{COVER_SUBTITLE}}": trip_data.get("subtitle", ""),
            "{{ORIGIN}}": trip_data.get("origin", "") or trip_data.get("departure", ""),
            "{{DATE_RANGE}}": trip_data.get("dateRange", ""),
            "{{DURATION}}": duration,
            "{{TRAVELERS}}": trip_data.get("travelers", ""),
            "{{TOTAL_BUDGET}}": str(trip_data.get("budget", {}).get("total", 0)),
            "{{PER_PERSON}}": str(trip_data.get("budget", {}).get("perPerson", 0)),
            "{{GENERATION_DATE}}": trip_data.get("generationDate", ""),
        }
        for placeholder, value in replacements.items():
            html = html.replace(placeholder, value)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated HTML: {output_path} (template={os.path.basename(template_path)})")


def generate_pdf_from_html(html_path: str, pdf_path: str, chrome_path: Optional[str] = None):
    chrome = chrome_path or _find_chrome_binary()
    if not chrome:
        raise RuntimeError(
            "No Chromium-based browser found. Install Chrome/Chromium/Edge/Brave "
            "or pass --chrome-path explicitly."
        )

    abs_html = Path(html_path).resolve()
    abs_pdf = Path(pdf_path).resolve()
    abs_pdf.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        # 尽量等 compositor 稳定再栅格化，减少大页缺块
        "--run-all-compositor-stages-before-draw",
        # 给远程图片留出加载时间；否则无头打印常得到「大半缺图」的 PDF
        "--virtual-time-budget=30000",
        "--no-pdf-header-footer",
        f"--print-to-pdf={abs_pdf}",
        f"file://{abs_html}",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    print(f"Generated PDF: {abs_pdf}")


class RoadbookHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, html_path=None, json_path=None, **kwargs):
        self.html_path = html_path
        self.json_path = json_path
        kwargs.setdefault("directory", os.path.dirname(os.path.abspath(html_path)) if html_path else os.getcwd())
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            # 动态读取最新 tripData.json 注入到 HTML 中
            with open(self.html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            with open(self.json_path, 'r', encoding='utf-8') as f:
                latest_json = f.read()
            # 用标记精准替换
            pattern = r'/\*TRIPDATA_START\*/[\s\S]*?/\*TRIPDATA_END\*/'
            lj = latest_json.strip()
            replacement = f"/*TRIPDATA_START*/\nconst tripData = {lj};\n/*TRIPDATA_END*/"
            new_html = re.sub(pattern, replacement, html_content, count=1)
            self.wfile.write(new_html.encode('utf-8'))
        elif self.path == '/tripdata':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with open(self.json_path, 'rb') as f:
                self.wfile.write(f.read())
        elif self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/save':
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                with open(self.json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "保存成功"}).encode())
                print(f"  [保存] tripData.json 已更新")
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        # 仅显示 /save 相关日志，静默其他请求
        try:
            msg = format % args
            if '/save' in msg:
                print(f"  {msg}")
        except:
            pass


def start_server(html_path, json_path, port=8888):
    handler = partial(
        RoadbookHandler,
        html_path=os.path.abspath(html_path),
        json_path=os.path.abspath(json_path),
    )
    server = http.server.HTTPServer(('127.0.0.1', port), handler)
    url = f'http://localhost:{port}'
    print(f"\n  路书预览服务已启动: {url}")
    print(f"  数据文件: {json_path}")
    print(f"  按 Ctrl+C 退出\n")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate travel plan HTML (and optional PDF) from tripData JSON."
    )
    parser.add_argument("trip_data_json", help="Path to tripData.json")
    parser.add_argument("output_html", help="Path to generated HTML")
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also generate PDF from the generated HTML",
    )
    parser.add_argument(
        "--pdf-path",
        default=None,
        help="Output PDF path (default: same name as HTML with .pdf)",
    )
    parser.add_argument(
        "--chrome-path",
        default=None,
        help="Optional explicit browser binary path for PDF generation",
    )
    parser.add_argument(
        "--auto-images",
        action="store_true",
        help="自动搜图填充：默认经 TikHub API 走小红书；缺图时不自动维基，需 `--image-fallback wikimedia` 等开启补图。",
    )
    parser.add_argument(
        "--min-images",
        type=int,
        default=3,
        help="Minimum number of images to auto fill (default: 3)",
    )
    parser.add_argument(
        "--save-updated-json",
        action="store_true",
        help="把内存中更新后的 tripData 写回第一个参数指定的 JSON（--auto-images 或默认本地化配图后均适用）",
    )
    parser.add_argument(
        "--ensure-day-images",
        action="store_true",
        help="Try to ensure each day has at least one activity image",
    )
    parser.add_argument(
        "--min-images-per-activity",
        type=int,
        default=1,
        help="Minimum number of images per activity (default: 1)",
    )
    parser.add_argument(
        "--strict-images",
        action="store_true",
        help="Only keep reliable image sources; disable random fallback",
    )
    parser.add_argument(
        "--image-provider",
        choices=("xiaohongshu", "wikimedia"),
        default="xiaohongshu",
        help="With --auto-images: xiaohongshu=TikHub 小红书（默认）；wikimedia=仅维基共享资源。",
    )
    parser.add_argument(
        "--image-fallback",
        choices=("none", "wikimedia", "full"),
        default="none",
        help="主源小红书缺图时：none=不自动补（默认，仅小红书）；wikimedia=维基；full=维基+loremflickr。",
    )
    parser.add_argument(
        "--image-timeout-ms",
        type=int,
        default=240000,
        help="With --auto-images: TikHub 搜图单次请求超时毫秒（默认 240000）。",
    )
    parser.add_argument(
        "--enrich-max-queries",
        type=int,
        default=220,
        help="路书 v2 + 主源为小红书：每个图片槽位的**小红书**关键词尝试上限（默认 220，按槽独立）。",
    )
    parser.add_argument(
        "--enrich-commons-queries",
        type=int,
        default=0,
        help="路书 v2：主源小红书失败后的**维基共享资源**单槽尝试上限；0 表示自动（约为主源上限的 1/9，14～40）。",
    )
    parser.add_argument(
        "--image-registry",
        default=None,
        help="Path to source-prioritized image registry JSON (default: assets/image_registry.sample.json when available)",
    )
    parser.add_argument(
        "--no-refresh-images",
        dest="refresh_images",
        action="store_false",
        help="With --auto-images: keep existing image URLs instead of re-fetching/overwriting",
    )
    parser.add_argument(
        "--refresh-images",
        dest="refresh_images",
        action="store_true",
        help="With --auto-images: re-fetch and overwrite images even when already populated (default: on)",
    )
    parser.set_defaults(refresh_images=True)
    # 图片本地化：默认开启（避免外链不稳定导致背景图等加载失败）
    parser.add_argument(
        "--localize-images",
        dest="localize_images",
        action="store_true",
        help="Download remote images next to the output HTML and rewrite image URLs to local files (default: on)",
    )
    parser.add_argument(
        "--no-localize-images",
        dest="localize_images",
        action="store_false",
        help="Disable image localization; keep remote image URLs in the output",
    )
    parser.set_defaults(localize_images=True)
    parser.add_argument(
        "--image-assets-dir",
        default="roadbook-images",
        help="Directory name for localized images, relative to output HTML (default: roadbook-images)",
    )
    parser.add_argument(
        "--localize-workers",
        type=int,
        default=16,
        help="Concurrent download workers for image localization (default: 16)",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Template alias (default/brochure/roadbook/roadbook-v2) or path to a custom .html template",
    )
    parser.add_argument(
        "--no-print-layout",
        action="store_true",
        help="roadbook-v2：不写入 meta.printLayout 预分页启发式（仍保留模板 @page / 打印 CSS）",
    )
    parser.add_argument(
        "--no-content-length-warn",
        action="store_true",
        help="roadbook-v2：跳过文案软上限 WARN（默认会检查并在 stderr 提示）",
    )
    parser.add_argument(
        '--serve',
        action='store_true',
        help="生成后启动本地预览服务",
    )
    parser.add_argument(
        '--no-serve',
        action='store_true',
        help="路书模板时不自动启动预览服务",
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8888,
        help="预览服务端口 (默认 8888)",
    )
    parser.add_argument(
        '--no-open',
        action='store_true',
        help="生成后不自动在浏览器打开 HTML",
    )
    args = parser.parse_args()

    temp_trip_data_path: Optional[str] = None
    # 第一个位置参数：交付/手工跑图时用于回写本地化后的配图路径（避免仅 HTML 指向 roadbook-images/ 而 tripData 仍为 https）
    user_trip_source_path = os.path.abspath(args.trip_data_json)
    try:
        if args.auto_images:
            with open(args.trip_data_json, "r", encoding="utf-8") as f:
                trip_data = json.load(f)
            registry_path = args.image_registry or _default_image_registry_path()
            image_registry = _load_image_registry(registry_path)
            if registry_path:
                print(f"Image registry: {registry_path}")
            hotel_info_filled = apply_hotel_source_info(trip_data, image_registry)
            if hotel_info_filled:
                print(f"Hotel info source filled: {hotel_info_filled}")
            print(
                f"Auto image provider={args.image_provider}, fallback={args.image_fallback}, "
                f"timeout_ms={args.image_timeout_ms}"
            )
            # 根据数据版本选择不同的图片填充逻辑
            meta = trip_data.get("meta", {})
            if meta.get("version") == "2.0" and "components" in trip_data:
                commons_cap = (
                    None
                    if int(args.enrich_commons_queries) <= 0
                    else max(8, int(args.enrich_commons_queries))
                )
                filled = enrich_images_v2(
                    trip_data,
                    strict_images=args.strict_images,
                    image_registry=image_registry,
                    max_queries=max(8, int(args.enrich_max_queries)),
                    commons_max_queries=commons_cap,
                    refresh_all=args.refresh_images,
                    image_provider=args.image_provider,
                    image_fallback=args.image_fallback,
                    image_timeout_ms=args.image_timeout_ms,
                )
            else:
                filled = enrich_images(
                    trip_data,
                    min_images=max(args.min_images, 1),
                    ensure_day_images=args.ensure_day_images,
                    min_images_per_activity=max(args.min_images_per_activity, 1),
                    strict_images=args.strict_images,
                    image_registry=image_registry,
                    image_provider=args.image_provider,
                    image_fallback=args.image_fallback,
                    image_timeout_ms=args.image_timeout_ms,
                )
            print(f"Auto image search filled: {filled}")
            if args.localize_images:
                localized, failed = localize_trip_images(
                    trip_data,
                    args.output_html,
                    assets_dir_name=args.image_assets_dir,
                    workers=args.localize_workers,
                )
                print(f"Localized images: {localized}, failed: {failed}")
            if args.save_updated_json:
                _apply_v2_tripdata_export_prep(trip_data, log=False)
                with open(args.trip_data_json, "w", encoding="utf-8") as f:
                    json.dump(trip_data, f, ensure_ascii=False, indent=2)
                print(f"Updated tripData JSON saved: {args.trip_data_json}")
            else:
                # Generate from enriched in-memory data without overwriting source file.
                _apply_v2_tripdata_export_prep(trip_data, log=False)
                temp_path = str(Path(args.output_html).with_suffix(".tmp.tripData.json"))
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(trip_data, f, ensure_ascii=False, indent=2)
                args.trip_data_json = temp_path
                temp_trip_data_path = temp_path
        elif args.localize_images:
            with open(args.trip_data_json, "r", encoding="utf-8") as f:
                trip_data = json.load(f)
            localized, failed = localize_trip_images(
                trip_data,
                args.output_html,
                assets_dir_name=args.image_assets_dir,
                workers=args.localize_workers,
            )
            print(f"Localized images: {localized}, failed: {failed}")
            if args.save_updated_json:
                _apply_v2_tripdata_export_prep(trip_data, log=False)
                with open(user_trip_source_path, "w", encoding="utf-8") as f:
                    json.dump(trip_data, f, ensure_ascii=False, indent=2)
                print(f"Updated tripData JSON saved: {user_trip_source_path}")
                args.trip_data_json = user_trip_source_path
            else:
                _apply_v2_tripdata_export_prep(trip_data, log=False)
                temp_path = str(Path(args.output_html).with_suffix(".tmp.tripData.json"))
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(trip_data, f, ensure_ascii=False, indent=2)
                args.trip_data_json = temp_path
                temp_trip_data_path = temp_path

        generate_html(
            args.trip_data_json,
            args.output_html,
            template=args.template,
            print_layout_hints=not args.no_print_layout,
            content_length_warnings=not args.no_content_length_warn,
        )
        if args.pdf:
            pdf_out = args.pdf_path or str(Path(args.output_html).with_suffix(".pdf"))
            generate_pdf_from_html(args.output_html, pdf_out, args.chrome_path)
        if temp_trip_data_path:
            try:
                os.remove(temp_trip_data_path)
            except Exception:
                pass

        # 判断是否使用了路书模板（显式指定或自动检测）
        is_roadbook = _is_roadbook_template(args.template) or _is_roadbook_v2_template(args.template)
        if not is_roadbook and not args.template:
            # 未显式指定模板时，检查是否被自动检测为 roadbook
            with open(sys.argv[1], "r", encoding="utf-8") as _f:
                _td = json.load(_f)
            detected = _auto_detect_template(_td)
            is_roadbook = detected in ("roadbook", "roadbook-v2")

        # 如果是路书模板，自动启动预览服务（除非明确传了 --no-serve）
        original_json = sys.argv[1]
        served = False
        if is_roadbook and not args.no_serve:
            served = True
            start_server(args.output_html, original_json, port=args.port)
        elif args.serve:
            served = True
            start_server(args.output_html, original_json, port=args.port)

        # 兵底：未走预览服务时，默认自动打开浏览器（可用 --no-open 禁用）
        if not served and not args.no_open:
            abs_html = os.path.abspath(args.output_html)
            try:
                # 中文路径需 percent-encoding，Path.as_uri() 会自动处理
                file_url = Path(abs_html).as_uri()
                opened = False
                # macOS: 优先用系统 open 命令，避开 webbrowser+AppleScript 在中文路径上的 -43 问题
                if sys.platform == "darwin":
                    try:
                        subprocess.run(["open", abs_html], check=True)
                        opened = True
                    except Exception:
                        opened = False
                if not opened:
                    opened = webbrowser.open(file_url)
                if opened:
                    print(f"已在浏览器打开: {file_url}")
                else:
                    print(f"未能自动打开浏览器，请手动访问: {file_url}")
            except Exception as _e:
                print(f"自动打开浏览器失败: {_e}")
    except subprocess.CalledProcessError as e:
        print("Failed to generate PDF via headless browser.")
        if e.stderr:
            print(e.stderr.strip())
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
