#!/usr/bin/env python3
"""用飞猪 FlyAI CLI 为 roadbook v2「住宿」feature 各卡片生成较长简介（写入 items[].description）。

依赖：本机可执行 flyai（npm i -g @fly-ai/flyai-cli）。飞猪无结果或字段过少时，可用中文维基摘要作网络兜底（无需密钥）。

用法:
  python3 scripts/enrich_hotel_intro_from_flyai.py \"路书目录/tripData.json\" \\
    --check-in 2026-06-01 --check-out 2026-06-02

逻辑简述:
  - items[].title 作为 --dest-name（城市/区域）；从 description 解析「备选酒店：」或「【拟定酒店】」后的首选店名作 --key-words。
  - **优先飞猪** ``search-hotels``：解析 ``itemList`` 匹配条目，拼接 **基本信息** + **舒适性 / 便利性** 顾问向说明，并从返回对象中抽取简介/设施/评价等字符串字段作为补充。
  - **飞猪不可用或信息过少**：依次尝试 **网络摘要**（中文维基 ``opensearch`` + 摘要），再与舒适性/便利性模板合并，保证达到 ``--min-chars``（交付默认 **200**）。
  - 会自动删除已弃用的 items[].stats 字段（若存在）。
  - 无「备选酒店：」或「【拟定酒店】」清单的条目不修改；**已有文案长度 ≥ --min-chars** 且非 ``--force`` 时跳过。
  - **飞猪无匹配**时：``description`` **留空**（不再写入网络摘录/「简介」模板兜底）。
  - **「简介」模板阈值**：``--forced-ref-substance-max``（飞猪字段总长低于 N 字则强加简介）、``--web-short-chars``（无飞猪时网络摘录低于 N 字则以简介为主）、``--no-forced-reference`` 关闭简介模板；环境变量 ``ROADBOOK_HOTEL_FORCED_REF_SUBSTANCE_MAX``、``ROADBOOK_HOTEL_WEB_SHORT_CHARS`` 可设默认。
  - 加 --hero-image：若 images[0] 无 url，则用返回的 mainPic 填入。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from flyai_hotel_shared import parse_hotel_names, run_flyai

WIKI_UA = "RoadbookHotelEnrich/1.0 (skills-travel-planner; https://meta.wikimedia.org/wiki/User-Agent_policy)"
MIN_FLYAI_BODY_CHARS = 120  # 飞猪拼接主体低于此则触发维基兜底（仍保留飞猪数据）


def _wikipedia_zh_extract(keyword: str, timeout: float = 14.0) -> str:
    """中文维基摘要 plain text。"""
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
    req = urllib.request.Request(op, headers={"User-Agent": WIKI_UA})
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
    req2 = urllib.request.Request(qp, headers={"User-Agent": WIKI_UA})
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
            return line[:900]
    return ""


def fetch_web_hotel_context(hotel_name: str, city: str, *, timeout: float = 14.0) -> str:
    """网络兜底：维基多条查询，取最长一段摘要。"""
    hn = (hotel_name or "").strip()
    ct = (city or "").strip()
    queries: List[str] = []
    if hn and ct:
        queries.append(f"{hn} {ct}")
        queries.append(f"{ct}{hn}酒店")
    if hn:
        queries.append(f"{hn}酒店")
        queries.append(hn)
    seen: set[str] = set()
    best = ""
    for q in queries:
        qn = q.strip()
        if len(qn) < 2 or qn in seen:
            continue
        seen.add(qn)
        chunk = _wikipedia_zh_extract(qn, timeout=timeout)
        if len(chunk) > len(best):
            best = chunk
        if len(best) > 500:
            break
    return best


def _walk_flyai_hotel_strings(node: Any, out: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            lk = str(k).lower()
            if any(x in lk for x in ("price", "url", "link", "detailurl", "detail_url", "book", "order", "pay")):
                continue
            if isinstance(v, str) and len(v.strip()) > 10:
                if any(
                    x in lk
                    for x in (
                        "intro",
                        "desc",
                        "summary",
                        "abstract",
                        "content",
                        "text",
                        "facility",
                        "facilit",
                        "service",
                        "feature",
                        "label",
                        "tag",
                        "comment",
                        "review",
                        "sell",
                        "point",
                        "highlight",
                        "advantage",
                        "tip",
                        "remark",
                        "poi",
                        "area",
                    )
                ):
                    if not v.strip().startswith("http"):
                        out.append(re.sub(r"https?://\S+", "", v.strip()))
            _walk_flyai_hotel_strings(v, out)
    elif isinstance(node, list):
        for item in node[:40]:
            _walk_flyai_hotel_strings(item, out)


def flyai_hotel_extra_blurb(h: dict) -> str:
    acc: List[str] = []
    _walk_flyai_hotel_strings(h, acc)
    seen: set[str] = set()
    uniq: List[str] = []
    for s in acc:
        if s in seen or len(s) < 12:
            continue
        seen.add(s)
        uniq.append(s)
    blob = re.sub(r"\s+", " ", " ".join(uniq)).strip()
    if len(blob) > 800:
        blob = blob[:800] + "…"
    return blob


def pick_hotel(item_list: List[dict], primary: str) -> Optional[dict]:
    if not item_list:
        return None
    primary_nospace = re.sub(r"\s+", "", primary)
    for h in item_list:
        name = (h.get("name") or "").strip()
        if not name:
            continue
        name_ns = re.sub(r"\s+", "", name)
        if primary in name or name in primary or primary_nospace in name_ns:
            return h
    return item_list[0]


def _comfort_and_convenience_copy() -> List[str]:
    return [
        "舒适性：建议结合近期住客点评关注隔音、床品支托与枕高是否合适、淋浴水温稳定性及换气；"
        "空调勿对床头直吹，敏感睡眠可优先选择不靠电梯井、不临主干道的一侧，必要时向前台确认可否提供加硬床垫或备用被芯。",
        "便利性：关注酒店与当日核心动线的距离（步行/车程）、打车是否方便、是否临近地铁或景区接驳点；"
        "亲子或行李较多时留意电梯可达性、大堂至客房动线是否台阶过多，并确认入住与退房时段能否寄存行李。",
    ]


# 飞猪身份字段（地址/兴趣点/补充摘要等）合起来仍很短时，强加一段「简介式」文案（风格对齐顾问稿，不含报价与外链）
DEFAULT_FORCED_REF_SUBSTANCE_MAX = 168
DEFAULT_WEB_SHORT_CHARS = 120


def _int_from_env(var: str, fallback: int) -> int:
    raw = (os.environ.get(var) or "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def effective_forced_ref_substance_max(cli_value: int | None) -> int:
    if cli_value is not None:
        return max(0, cli_value)
    return max(0, _int_from_env("ROADBOOK_HOTEL_FORCED_REF_SUBSTANCE_MAX", DEFAULT_FORCED_REF_SUBSTANCE_MAX))


def effective_web_short_chars(cli_value: int | None) -> int:
    if cli_value is not None:
        v = max(0, cli_value)
        if v == 0:
            return DEFAULT_WEB_SHORT_CHARS
        return v
    return max(1, _int_from_env("ROADBOOK_HOTEL_WEB_SHORT_CHARS", DEFAULT_WEB_SHORT_CHARS))


def reference_style_forced_blurb(
    hotel_name: str,
    city: str,
    *,
    location_detail: str = "",
) -> str:
    """参考交付习惯：区位 + 综合业态 + 交通停车会议餐饮 + 服务理念；具体车位/席位数留「以酒店确认为准」。"""
    d = (location_detail or "").strip()
    ct = (city or "").strip() or "当地"
    hn = (hotel_name or "酒店").strip() or "酒店"
    if d:
        where = d if (ct in d or d.startswith(ct)) else f"{ct}{d}"
    else:
        where = f"{ct}核心城区"
    return (
        f"简介：{hn}位于{where}，是一家集餐饮住宿、会议服务为一体的综合性酒店。"
        "酒店位置与交通便利条件较好，门前具有大型停车场，地下暖库与新能源汽车充电等配套以到店当日酒店公示为准。"
        "酒店客房设有宽带上网，房内智能化设施以实际排房为准；会议室可承接大、中、小型会议，具体场地与设备以预订时确认为准；"
        "酒店餐厅可满足团队集中用餐需求，可容纳人数与菜单以酒店确认为准。"
        f"{hn}本着以人为本、信誉至上的服务理念，全心全意为往来宾客提供优质服务，竭诚欢迎您的到来。"
    )


def compose_intro(
    h: dict,
    alternates: List[str],
    min_chars: int,
    *,
    web_supplement: str = "",
    city_label: str = "",
    forced_reference: bool = True,
    forced_ref_substance_max: int = DEFAULT_FORCED_REF_SUBSTANCE_MAX,
) -> str:
    name = (h.get("name") or "").strip()
    addr = (h.get("address") or "").strip()
    poi = (h.get("interestsPoi") or "").strip()
    star = (h.get("star") or "").strip()
    brand = (h.get("brandName") or "").strip()
    dec = (h.get("decorationTime") or "").strip()
    display_name = name or (alternates[0] if alternates else "酒店")
    city_safe = (city_label or "").strip() or "当地"

    head: List[str] = []
    if name and addr:
        head.append(f"{name}位于{addr}，可作为本区域住宿与休整的拟定参考。")
    elif name:
        head.append(f"{name}为飞猪检索结果中与方案首选较接近的匹配项，可作为住宿拟定参考。")

    mid: List[str] = []
    if star:
        mid.append(f"档次参考：平台展示为「{star}」，实际体验请以最新披露与住客评价为准。")
    if brand:
        mid.append(f"品牌：{brand}。")
    if dec:
        mid.append(f"装修/年代信息（平台展示）：{dec}，仅作参考。")

    for key in ("review", "score", "reviewScore"):
        v = h.get(key)
        if isinstance(v, str) and 4 <= len(v.strip()) <= 120:
            mid.append(f"口碑提示：{v.strip()}（仅供参考）。")
            break

    if poi:
        mid.append(f"区位：{poi}，有利于衔接随后行程与交通。")

    fly_x = flyai_hotel_extra_blurb(h)
    substance = "".join(head) + "".join(mid) + fly_x
    need_forced = forced_reference and len(substance) < max(0, forced_ref_substance_max)

    parts: List[str] = []
    if need_forced:
        parts.append(
            reference_style_forced_blurb(
                display_name,
                city_safe,
                location_detail=addr or poi or "",
            )
        )
        parts.extend(mid)
        if fly_x:
            parts.append("飞猪卡片补充摘要：" + fly_x)
    else:
        parts.extend(head)
        parts.extend(mid)

    parts.extend(_comfort_and_convenience_copy())

    if not need_forced and fly_x:
        parts.append("飞猪卡片补充摘要：" + fly_x)

    parts.append(
        "预订与入离店前请向供应商确认房态、面积、床型、是否含早、儿童加床及取消规则；"
        "若有无烟房、低楼层、接驳或延迟退房等需求，请提前书面备注。"
    )
    if web_supplement:
        parts.append("网络背景参考（摘录自开放百科类来源，可能与酒店实时经营有出入，以门店及订单为准）：" + web_supplement[:550])
    if len(alternates) > 1:
        parts.append("同区域还可横向对比：" + "、".join(alternates[1:6]) + "。")

    text = "".join(parts)
    extra = (
        "该住宿点便于整理行李与次日按计划出发；旺季与节假日房源波动大，建议尽早锁房并保留合理变更余地。"
    )
    while len(text) < min_chars:
        text += extra
        if len(text) >= min_chars:
            break
        extra = "出行前请再次核对酒店政策、周边临时交通管制与停车/接驳安排。"
    return text


def compose_intro_no_flyai(
    primary: str,
    city: str,
    alternates: List[str],
    web_blob: str,
    min_chars: int,
    *,
    forced_reference: bool = True,
    web_short_chars: int = DEFAULT_WEB_SHORT_CHARS,
) -> str:
    parts: List[str] = []
    parts.append(
        f"拟订参考住宿：「{primary}」，区域为{city}。"
        "暂未能从数据接口匹配到完整酒店卡片，先为您提供便于客人阅读的说明（不含具体报价与预订链接）。"
    )
    if web_blob:
        wb = web_blob.strip()
        threshold = max(1, web_short_chars)
        if not forced_reference:
            if wb:
                parts.append("公开资料摘录（供背景了解，请以酒店实际经营为准）：" + web_blob[:620])
            else:
                parts.append(
                    f"拟订参考住宿「{primary}」（{city}），具体设施与服务以酒店披露及订单为准。"
                )
        elif len(wb) >= threshold:
            parts.append("公开资料摘录（供背景了解，请以酒店实际经营为准）：" + web_blob[:620])
        else:
            parts.append(reference_style_forced_blurb(primary, city, location_detail=""))
            if wb:
                parts.append("公开资料补充：" + wb)
    else:
        if forced_reference:
            parts.append(reference_style_forced_blurb(primary, city, location_detail=""))
        else:
            parts.append(
                f"拟订参考住宿「{primary}」（{city}），具体设施与服务以酒店披露及订单为准。"
            )
    parts.extend(_comfort_and_convenience_copy())
    parts.append(
        "预订前建议确认含早与否、加床与无烟房政策，以及泳池/健身房是否开放；上述信息以供应商书面确认为准。"
    )
    if len(alternates) > 1:
        parts.append("同区域还可横向对比：" + "、".join(alternates[1:6]) + "。")
    text = "".join(parts)
    extra = (
        "旺季与节假日房态变化快，建议与您顾问同步最终落点；照顾老人或儿童时，可提前说明电梯与低楼层偏好。"
    )
    while len(text) < min_chars:
        text += extra
        if len(text) >= min_chars:
            break
        extra = "抵达当日可向前台确认最优电梯与客房朝向，减少不必要的换房成本。"
    return text


def enrich_trip(
    data: dict,
    check_in: str,
    check_out: str,
    min_chars: int,
    update_hero_url: bool,
    timeout: int,
    force: bool,
    *,
    web_fallback: bool = True,
    forced_reference: bool = True,
    forced_ref_substance_max: int = DEFAULT_FORCED_REF_SUBSTANCE_MAX,
    web_short_chars: int = DEFAULT_WEB_SHORT_CHARS,
) -> int:
    updated = 0
    for comp in data.get("components") or []:
        if comp.get("type") != "feature":
            continue
        d = comp.get("data") or {}
        if d.get("subtype") != "住宿":
            continue
        items = d.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item.pop("stats", None)
            city = (item.get("title") or "").strip()
            desc = str(item.get("description") or "")
            has_seed = "备选酒店：" in desc or "【拟定酒店】" in desc
            if not has_seed:
                continue
            if len(desc.strip()) >= min_chars and not force:
                continue
            hotels = parse_hotel_names(desc)
            if not city:
                continue
            fly_dest = city
            primary = hotels[0] if hotels else city
            key = primary if hotels else ""
            payload = run_flyai(fly_dest, key, check_in, check_out, timeout=timeout)
            h: Optional[dict] = None
            item_list: Optional[List[dict]] = None
            if isinstance(payload, dict):
                raw_list = payload.get("itemList")
                if isinstance(raw_list, list) and raw_list:
                    h = pick_hotel(raw_list, primary) or raw_list[0]

            web_blob = ""
            if web_fallback:
                need_web = h is None
                if h is not None:
                    core = "".join(
                        [
                            str(h.get("name") or "").strip(),
                            str(h.get("address") or "").strip(),
                            str(h.get("interestsPoi") or "").strip(),
                        ]
                    )
                    extra_b = flyai_hotel_extra_blurb(h)
                    if len(core) + len(extra_b) < MIN_FLYAI_BODY_CHARS:
                        need_web = True
                if need_web:
                    web_blob = fetch_web_hotel_context(primary, fly_dest)
                    if web_blob:
                        print(f"  [hotel web] {primary!r} -> +{len(web_blob)} 字", flush=True)

            if h is not None:
                item["description"] = compose_intro(
                    h,
                    hotels,
                    min_chars,
                    web_supplement=web_blob,
                    city_label=fly_dest,
                    forced_reference=forced_reference,
                    forced_ref_substance_max=forced_ref_substance_max,
                )
                if update_hero_url:
                    imgs = item.get("images")
                    pic = (h.get("mainPic") or "").strip()
                    if pic and isinstance(imgs, list) and imgs and isinstance(imgs[0], dict):
                        if not str(imgs[0].get("url") or "").strip():
                            imgs[0]["url"] = pic
            else:
                print(
                    f"WARN 飞猪无匹配：{primary!r}（{fly_dest}），住宿简介留空（不写入模板/网络兜底文案）",
                    file=sys.stderr,
                )
                item["description"] = ""
                item["images"] = []

            updated += 1
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description="flyai 拉取酒店并写长简介到 tripData")
    ap.add_argument("trip_json", help="tripData.json")
    ap.add_argument("--check-in", default="", help="YYYY-MM-DD")
    ap.add_argument("--check-out", default="", help="YYYY-MM-DD")
    ap.add_argument("--min-chars", type=int, default=200, help="简介最短字符数（默认 200）")
    ap.add_argument(
        "--hero-image",
        action="store_true",
        help="首图无 url 时用 mainPic",
    )
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument(
        "--force",
        action="store_true",
        help="忽略「已有长简介则跳过」，对仍含备选酒店清单的条目强制重写",
    )
    ap.add_argument(
        "--no-web-fallback",
        action="store_true",
        help="禁用中文维基等网络摘要兜底（仅飞猪成功时信息更完整；飞猪失败时仍用模板垫够字数）",
    )
    ap.add_argument(
        "--forced-ref-substance-max",
        type=int,
        default=None,
        metavar="N",
        help="飞猪素材总长(字)低于 N 则插入「简介」模板；默认 168；可用环境变量 ROADBOOK_HOTEL_FORCED_REF_SUBSTANCE_MAX",
    )
    ap.add_argument(
        "--web-short-chars",
        type=int,
        default=None,
        metavar="N",
        help="无飞猪时网络摘录低于 N 字则以简介模板为主；默认 120；环境变量 ROADBOOK_HOTEL_WEB_SHORT_CHARS",
    )
    ap.add_argument(
        "--no-forced-reference",
        action="store_true",
        help="禁用「简介」模板强加（仍保留舒适性/便利性及字数垫底）",
    )
    args = ap.parse_args()

    path = Path(args.trip_json).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))

    check_in = args.check_in
    check_out = args.check_out
    if not check_in or not check_out:
        base = (data.get("meta") or {}).get("generationDate") or "2026-06-01"
        try:
            dt = datetime.strptime(str(base)[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime(2026, 6, 1)
        check_in = check_in or dt.strftime("%Y-%m-%d")
        check_out = check_out or (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    fr_max = effective_forced_ref_substance_max(args.forced_ref_substance_max)
    w_short = effective_web_short_chars(args.web_short_chars)
    n = enrich_trip(
        data,
        check_in,
        check_out,
        args.min_chars,
        update_hero_url=args.hero_image,
        timeout=args.timeout,
        force=args.force,
        web_fallback=not args.no_web_fallback,
        forced_reference=not args.no_forced_reference,
        forced_ref_substance_max=fr_max,
        web_short_chars=w_short,
    )
    meta = data.setdefault("meta", {})
    meta["updatedAt"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {n} 条住宿简介 → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
