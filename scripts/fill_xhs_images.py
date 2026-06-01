#!/usr/bin/env python3
"""Fill roadbook v2 image slots from Xiaohongshu via **TikHub API**（``api.tikhub.io``）.

仓库根 ``.env`` 须配置 ``TIKHUB_API_KEY``。默认在批量跑槽前执行 **search_notes 预检**（可用 --no-preflight 跳过）。
``cover.logo`` **固定为品牌 Logo 占位路径**（``roadbook-images/logo-brand-wdtrip.png``，**不调小红书**；接入自有后端时可转存对象存储）。
``subtype`` 为 **费用 / 服务 / 须知** 的 ``text-block`` 配图槽 **不调用小红书**（清空 ``url``/``alternates``），与模板「费用/服务白板、须知无章节底图」一致。

交付标准化（由 deliver_roadbook_v2.py 默认打开 **--require-remote-urls**）：
优先写入本轮 MCP 返回的 **https** 图片 URL；不足时默认按 **飞猪 FlyAI → Wikimedia Commons → placehold.co 浅色占位** 补足（仍为 https），避免交付中断。
若显式加 **--no-image-fallback-chain**，则恢复「仅靠小红书 https，不足则退出码 1」。
不足则不合并本地 roadbook-images（除非关闭 strict 时的既有合并逻辑）。

写入形态：{"alternates": [...], "slotLabel": "..."}。
检索词策略见 ``scripts/xhs_search_keyword_rules.py``（封面/大标题背景、每日玩法、酒店、交通等分层）。

TikHub 请求节流：环境变量 ``ROADBOOK_FILL_XHS_COOLDOWN_MS``、``ROADBOOK_FILL_XHS_SLOT_GAP_MS``、``ROADBOOK_FILL_XHS_MAX_DETAIL_FEEDS``，或命令行 ``--xhs-cooldown-ms`` / ``--xhs-slot-gap-ms`` / ``--xhs-max-detail-feeds``（CLI 优先）。

**交通相关配图**（``subtype: 交通`` 的用车图 ``transport_gallery`` 与章节 ``backgroundImage``）：**固定仅走飞猪 FlyAI ``keyword-search``（网络）**，不调小红书；见 ``image_fallback_chain.flyai_transport_*``。

小红书 MCP 不足张数时（默认开启）：按 ``scripts/image_fallback_chain.py`` **飞猪 FlyAI（酒店 search-hotels / 景点 search-poi / 交通 keyword-search）→ Wikimedia Commons → placehold.co 浅色占位** 补足 https URL；可用 ``--no-image-fallback-chain`` 关闭并恢复「严格仅靠小红书」（交通槽仍走飞猪网络）。

TikHub 不可用（密钥无效 / 预检失败 / 配额用尽）时：可加 ``--skip-xhs`` **完全跳过小红书**，单槽直接走 flyai → Commons → placeholder 兜底链。供 ``deliver_roadbook_v2.py`` 在 fill 整体失败后自动二次降级使用。

**跨槽去重**：默认对小红书 CDN 图做 URL 指纹，并在整本书范围内排除已出现在其它槽的图片（同次运行与 JSON 内已有 URL 均纳入登记）；本槽当前持有的 URL 仍可用于回填。若需恢复旧行为（允许跨槽重复）：``export ROADBOOK_FILL_XHS_NO_CROSS_SLOT_DEDUPE=1``。

**可选视觉去重（pHash）**：``--visual-dedupe`` 或 ``ROADBOOK_FILL_VISUAL_DEDUPE=1``，依赖 ``pip install -r requirements-roadbook-images.txt``；缓存目录默认仓库下 ``cache/``，可用 ``ROADBOOK_IMAGE_CACHE_ROOT`` 覆盖。去重后若不足 ``--min-images``，会**按关键词表再扫一轮并可走兜底链**补拉（轮数上限为 ``len(检索词变体)``，仍不足则停）。单笔记取图上限：``--max-images-per-feed`` / ``ROADBOOK_FILL_XHS_MAX_IMAGES_PER_FEED``（默认 5）；每关键词轮询新增上限:``--max-images-per-keyword-attempt`` / ``ROADBOOK_FILL_XHS_MAX_IMAGES_PER_KEYWORD``（默认 2）。感知哈希距离阈值：``ROADBOOK_VDEDUP_PHASH_MAX``（默认 6）。

**并发取图（默认开启）**：``--xhs-concurrency`` / ``ROADBOOK_FILL_XHS_CONCURRENCY``（默认 4）控制 TikHub 取图线程数。
每个 worker 共享 ``tikhub_xhs_client`` 的 keep-alive ``httpx.Client``，``XhsNoteCache`` 已加锁保护。
- 第一轮并发跑所有槽，``slot_exclude`` 采用 **初始全书指纹快照**（避免线程间竞态）；
- 主线程按 idx 顺序合并结果，做**前向指纹去重**与 ``set_by_path`` 写回；
- 因前向去重而不足 ``--min-images`` 的槽，最后**串行补一轮**，``slot_exclude`` 用最新累积的指纹集。
- ``--visual-dedupe`` 启用时与并发互斥（SQLite phash 缓存非多线程安全），自动回退 concurrency=1。
"""


from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brand_logo import brand_logo_relative_url, is_cover_brand_logo_slot
from image_fallback_chain import (
    collect_fallback_urls,
    flyai_transport_mainpics,
    flyai_transport_section_bg_urls,
    resolve_transport_item_for_path,
)
from roadbook_image_alternate_defaults import resolved_alternate_bounds
from xhs_image_url_rules import (
    dedupe_urls_by_fingerprint,
    fingerprint_image_url,
    is_remote_https_image_url,
    normalize_xhs_image_url,
    register_url_fingerprints,
)
from xhs_search_keyword_rules import classify_image_slot, planned_search_keywords


def resolve_throttle_ms(cli_value: int, env_name: str, default_if_unset: int) -> int:
    """CLI 传入 >=0 时优先生效；否则读环境变量；无效或未设则用 default_if_unset。"""
    if cli_value >= 0:
        return max(0, cli_value)
    raw = os.environ.get(env_name, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return max(0, default_if_unset)


def resolve_positive_int(cli_value: int, env_name: str, default_positive: int) -> int:
    """CLI 传入 >=0 时优先生效；否则读环境变量数字；否则 default_positive（至少 1）。"""
    if cli_value >= 0:
        return max(1, cli_value)
    raw = os.environ.get(env_name, "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return max(1, default_positive)


def throttle_sleep_ms(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def run(cmd: list[str], cwd: Path, timeout: int) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def text_block_subtype_for_image_path(data: dict[str, Any], path: list[Any]) -> str | None:
    """若配图槽位于 ``components[*].type=='text-block'`` 的 ``data`` 下，返回 ``data.subtype``。"""
    try:
        ci = path.index("components")
    except ValueError:
        return None
    if ci + 1 >= len(path):
        return None
    idx = path[ci + 1]
    if not isinstance(idx, int):
        return None
    comps = data.get("components")
    if not isinstance(comps, list) or idx >= len(comps):
        return None
    comp = comps[idx]
    if not isinstance(comp, dict) or comp.get("type") != "text-block":
        return None
    dd = comp.get("data")
    if not isinstance(dd, dict):
        return None
    st = dd.get("subtype")
    return st if isinstance(st, str) else None


def is_fee_or_service_text_block_image_slot(data: dict[str, Any], path: list[Any]) -> bool:
    """费用 / 服务 / 出行须知章节不配章节配图（模板为白板或纯底正文）；按 text-block subtype 识别。"""
    return text_block_subtype_for_image_path(data, path) in {"费用", "服务", "须知"}


def _hotel_feature_component_for_path(
    data: dict[str, Any], path: list[Any]
) -> dict[str, Any] | None:
    try:
        ci = path.index("components")
        comp_idx = path[ci + 1]
        if not isinstance(comp_idx, int):
            return None
        comps = data.get("components")
        if not isinstance(comps, list) or not (0 <= comp_idx < len(comps)):
            return None
        comp = comps[comp_idx]
        return comp if isinstance(comp, dict) else None
    except (ValueError, IndexError, TypeError):
        return None


def should_skip_hotel_feature_imagery(data: dict[str, Any], path: list[Any]) -> bool:
    """住宿 feature 无有效简介（未匹配/无酒店信息）时不自动搜图，保持模块空白。"""
    comp = _hotel_feature_component_for_path(data, path)
    if not comp or comp.get("type") != "feature":
        return False
    dd = comp.get("data")
    if not isinstance(dd, dict) or dd.get("subtype") != "住宿":
        return False
    items = dd.get("items")
    if not isinstance(items, list) or len(items) == 0:
        return True
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("description") or "").strip():
            return False
    return True


def is_flyai_transport_only_slot(data: dict[str, Any], path: list[Any], slot_kind: str) -> bool:
    """交通 feature：用车图与章节背景固定走飞猪 keyword-search，不走小红书。"""
    if slot_kind == "transport_gallery":
        return True
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


def collect_image_entries(node: Any, path: list[Any] | None = None) -> list[tuple[list[Any], dict[str, Any]]]:
    path = path or []
    entries: list[tuple[list[Any], dict[str, Any]]] = []
    if isinstance(node, dict):
        if any(k in node for k in ("url", "alternates")) and isinstance(node.get("slotLabel"), str):
            entries.append((path, node))
        for key, value in node.items():
            entries.extend(collect_image_entries(value, path + [key]))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            entries.extend(collect_image_entries(value, path + [idx]))
    return entries


def get_by_path(node: Any, path: list[Any]) -> Any:
    cur = node
    for part in path:
        cur = cur[part]
    return cur


def set_by_path(node: Any, path: list[Any], value: Any) -> None:
    cur = node
    for part in path[:-1]:
        cur = cur[part]
    cur[path[-1]] = value


def existing_urls(entry: Any) -> list[str]:
    if isinstance(entry, str):
        return [entry] if entry else []
    if isinstance(entry, list):
        out: list[str] = []
        for item in entry:
            out.extend(existing_urls(item))
        return dedupe(out)
    if isinstance(entry, dict):
        out: list[str] = []
        url = entry.get("url")
        if isinstance(url, str) and url.strip():
            out.append(url.strip())
        if isinstance(entry.get("alternates"), list):
            for u in entry["alternates"]:
                if isinstance(u, str) and u.strip():
                    out.append(u.strip())
        return dedupe(out)
    return []


def dedupe(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def clean_keyword(slot_label: str) -> str:
    text = re.sub(r"\s+", " ", slot_label.replace("小红书", " ")).strip()
    return text or slot_label.strip()


def fetch_slot_images(
    root: Path,
    keyword: str,
    min_images: int,
    max_images: int,
    timeout_ms: int,
    *,
    retries: int = 3,
    cooldown_ms: int = 0,
    max_feed_details: int = 6,
    exclude_fingerprints: set[str] | None = None,
    max_images_per_feed: int = 5,
) -> tuple[list[str], list[dict[str, Any]]]:
    from tikhub_xhs_feeds import fetch_slot_images_via_tikhub

    return fetch_slot_images_via_tikhub(
        root,
        keyword,
        min_images,
        max_images,
        timeout_ms,
        retries=retries,
        cooldown_ms=cooldown_ms,
        max_feed_details=max_feed_details,
        exclude_fingerprints=exclude_fingerprints,
        max_images_per_feed=max_images_per_feed,
        normalize_url=normalize_xhs_image_url,
        fingerprint_image_url=fingerprint_image_url,
        throttle_sleep_ms=throttle_sleep_ms,
    )


def collect_phash_allowlist_from_urls(cache: Any, urls: list[str]) -> set[str]:
    """当前槽已有 URL 在缓存中对应的 phash，写入时允许保留（与旧 URL 视觉一致时不误杀）。"""
    out: set[str] = set()
    for raw in urls:
        u = normalize_xhs_image_url(str(raw or "").strip())
        if not u or not is_remote_https_image_url(u):
            continue
        row = cache.get_by_url(u)
        if row and row.get("phash"):
            out.add(str(row["phash"]))
    return out


def visual_dedupe_filter_urls(
    urls: list[str],
    *,
    cache: Any,
    book_phash_hex: set[str],
    slot_allow_phash: set[str],
    max_distance: int | None = None,
) -> tuple[list[str], list[str]]:
    """感知哈希去重：返回 (保留的 URL 列表, 新纳入全书的 phash hex 列表)。"""
    from roadbook_image_engine.visual_hash import compute_phash_hex, is_similar_phash_hex

    kept: list[str] = []
    round_hex: list[str] = []
    accepted: list[str] = []
    for url in urls:
        nu = normalize_xhs_image_url(str(url or "").strip())
        if not nu:
            continue
        if not is_remote_https_image_url(nu):
            kept.append(nu)
            continue
        row = cache.get_by_url(nu)
        if row and row.get("phash"):
            ph = str(row["phash"])
            w, h = row.get("width"), row.get("height")
        else:
            ph, w, h = compute_phash_hex(nu)
            cache.upsert(nu, phash=ph, width=w, height=h)
        reject = False
        for prev in round_hex:
            if is_similar_phash_hex(ph, prev, max_distance=max_distance):
                reject = True
                break
        if not reject:
            for g in book_phash_hex:
                if g in slot_allow_phash:
                    continue
                if is_similar_phash_hex(ph, g, max_distance=max_distance):
                    reject = True
                    break
        if reject:
            continue
        kept.append(nu)
        round_hex.append(ph)
        accepted.append(ph)
    return kept, accepted


@dataclass
class _SlotJob:
    """单个配图槽的不变上下文（主线程预计算，worker 只读）。"""

    idx: int
    path: list[Any]
    entry: dict[str, Any]
    label: str
    current_urls: list[str]
    current_fps: set[str]


@dataclass
class _SlotResult:
    """worker 返回的纯结果，由主线程顺序合并到 tripData。"""

    idx: int
    label: str
    keyword: str
    slot_kind: str
    urls: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    refill_round: int = 0
    visual_accepted: list[str] = field(default_factory=list)
    skip_fixed_brand_logo: bool = False
    skip_empty_hotel_feature: bool = False
    skip_fee_service_text_block: bool = False
    skip_existing: bool = False
    flyai_transport_only: bool = False
    attempted_fill: bool = False
    error: str | None = None


def _process_slot(
    *,
    job: _SlotJob,
    total: int,
    data: dict[str, Any],
    args: argparse.Namespace,
    root: Path,
    trip_path: Path,
    xhs_cooldown_ms: int,
    xhs_max_feeds: int,
    per_feed_cap: int,
    kw_cap: int,
    slot_exclude: set[str] | None,
    slot_phash_allow: set[str],
    book_phash_snapshot: set[str],
    vcache: Any,
    visual_dedupe: bool,
) -> _SlotResult:
    """处理单个槽，返回 ``_SlotResult``；不直接修改 ``data`` / 全局指纹集合。

    主线程负责按 idx 顺序合并：前向去重、写回 ``set_by_path``、累积 ``global_used`` / ``book_phash``。
    """
    idx = job.idx
    label = job.label
    current_urls = job.current_urls

    if is_cover_brand_logo_slot(job.path):
        print(f"[{idx}/{total}] skip XHS (封面固定品牌 Logo): {label}", flush=True)
        return _SlotResult(
            idx=idx, label=label, keyword="", slot_kind="",
            skip_fixed_brand_logo=True,
        )

    if should_skip_hotel_feature_imagery(data, job.path):
        print(f"[{idx}/{total}] skip XHS (住宿模块无简介，保持空白): {label}", flush=True)
        return _SlotResult(
            idx=idx, label=label, keyword="", slot_kind="",
            skip_empty_hotel_feature=True,
        )

    if is_fee_or_service_text_block_image_slot(data, job.path):
        print(f"[{idx}/{total}] skip XHS (费用/服务/须知正文页无需配图): {label}", flush=True)
        return _SlotResult(
            idx=idx, label=label, keyword="", slot_kind="",
            skip_fee_service_text_block=True,
        )

    slot_kind = classify_image_slot(data, job.path)
    flyai_transport_only = is_flyai_transport_only_slot(data, job.path, slot_kind)

    if (
        args.skip_existing
        and len(current_urls) >= args.min_images
        and not flyai_transport_only
    ):
        print(f"[{idx}/{total}] skip {label} ({len(current_urls)} existing)", flush=True)
        return _SlotResult(
            idx=idx, label=label, keyword="", slot_kind=slot_kind,
            skip_existing=True,
        )

    variants = planned_search_keywords(data, job.path, label)
    keyword = variants[0] if variants else clean_keyword(label)
    urls: list[str] = []
    sources: list[dict[str, Any]] = []
    visual_accepted: list[str] = []
    refill_round = 0
    attempted_fill = False
    kw_cursor = {"i": 0}

    def finalize_urls_pipeline(u: list[str]) -> tuple[list[str], list[str]]:
        remote_merge = [x for x in current_urls if is_remote_https_image_url(x)]
        if not args.require_remote_urls and len(u) < args.min_images:
            print(
                f"  ! only {len(u)} image(s), keep existing if available",
                file=sys.stderr,
            )
            u = dedupe(u + remote_merge + current_urls)
        if args.require_remote_urls:
            u = dedupe([x for x in u if is_remote_https_image_url(x)])
        if slot_exclude is not None:
            u = dedupe_urls_by_fingerprint(u, exclude_fingerprints=slot_exclude)
        u = u[: args.max_images]
        accepted: list[str] = []
        if visual_dedupe and vcache and u:
            n_bef = len(u)
            u, accepted = visual_dedupe_filter_urls(
                u,
                cache=vcache,
                book_phash_hex=book_phash_snapshot,
                slot_allow_phash=slot_phash_allow,
                max_distance=None,
            )
            u = u[: args.max_images]
            if n_bef > len(u):
                print(
                    f"  visual-dedupe: removed {n_bef - len(u)} near-duplicate(s)",
                    flush=True,
                )
        return u, accepted

    def run_fallback_chain(u: list[str]) -> list[str]:
        if (
            not args.no_image_fallback_chain
            and len(u) < args.min_images
        ):
            extra, fb_tags = collect_fallback_urls(
                prefix_urls=u,
                data=data,
                path=job.path,
                slot_kind=slot_kind,
                slot_label=str(label or ""),
                keyword=str(keyword or ""),
                trip_path=trip_path,
                min_images=args.min_images,
                max_images=args.max_images,
                flyai_timeout=max(15, args.flyai_timeout),
            )
            if fb_tags:
                print(
                    f"  fallback chain ({', '.join(fb_tags)}) +{len(extra)} url(s)",
                    flush=True,
                )
                sources.append({"fallback_chain": fb_tags})
            return dedupe(u + extra)
        return u

    def xhs_fetch_extend_urls(*, break_on_min_raw: bool) -> None:
        nonlocal urls
        while kw_cursor["i"] < len(variants):
            attempt_kw = variants[kw_cursor["i"]]
            kw_cursor["i"] += 1
            if attempt_kw != keyword:
                print(f"  alt keyword: {attempt_kw}", flush=True)
            room = args.max_images - len(urls)
            if room <= 0:
                return
            round_max = min(room, kw_cap)
            need_gap = args.min_images - len(urls)
            round_min = max(1, min(need_gap if need_gap > 0 else 1, round_max))
            if len(urls) >= args.min_images:
                round_min = 1
            try:
                cand_u, cand_s = fetch_slot_images(
                    root,
                    attempt_kw,
                    round_min,
                    round_max,
                    args.timeout_ms,
                    retries=args.slot_retries,
                    cooldown_ms=xhs_cooldown_ms,
                    max_feed_details=xhs_max_feeds,
                    exclude_fingerprints=slot_exclude,
                    max_images_per_feed=per_feed_cap,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  ! failed: {exc}", file=sys.stderr)
                cand_u, cand_s = [], []
            if cand_u:
                merged = dedupe_urls_by_fingerprint(
                    urls + cand_u,
                    exclude_fingerprints=slot_exclude,
                )[: args.max_images]
                urls = merged
                sources.extend(cand_s)
            if break_on_min_raw and len(urls) >= args.min_images:
                return
            if len(urls) >= args.max_images:
                return

    if flyai_transport_only:
        print(
            f"[{idx}/{total}] flyai-network [{slot_kind}]: {keyword} (跳过小红书)",
            flush=True,
        )
        flyai_to = max(15, args.flyai_timeout)
        if slot_kind == "transport_gallery":
            acc = resolve_transport_item_for_path(data, job.path)
            title = str((acc or {}).get("title") or label or "").strip()
            urls = flyai_transport_mainpics(
                item_title=title,
                slot_label=label or title,
                data=data,
                timeout=flyai_to,
                min_images=args.min_images,
            )
        else:
            urls = flyai_transport_section_bg_urls(
                data,
                flyai_to,
                min_images=args.min_images,
            )
        urls = urls[: args.max_images]
        if urls:
            sources.append({"source": "flyai_keyword_transport", "count": len(urls)})
    elif args.skip_xhs:
        print(f"[{idx}/{total}] skip-xhs [{slot_kind}]: {keyword}", flush=True)
    else:
        attempted_fill = True
        print(f"[{idx}/{total}] search [{slot_kind}]: {keyword}", flush=True)
        xhs_fetch_extend_urls(break_on_min_raw=True)

    if not args.no_image_fallback_chain and (
        not flyai_transport_only or len(urls) < args.min_images
    ):
        urls = run_fallback_chain(urls)
    urls, visual_accepted = finalize_urls_pipeline(urls)

    max_refill = max(1, len(variants))
    while (
        visual_dedupe
        and vcache
        and not args.skip_xhs
        and not flyai_transport_only
        and len(urls) < args.min_images
        and refill_round < max_refill
    ):
        prev_n = len(urls)
        refill_round += 1
        print(
            f"  visual-dedupe: need {args.min_images - len(urls)} more unique image(s) "
            f"(refill {refill_round}/{max_refill})",
            flush=True,
        )
        kw_cursor["i"] = 0
        xhs_fetch_extend_urls(break_on_min_raw=False)
        urls = run_fallback_chain(urls)
        urls, visual_accepted = finalize_urls_pipeline(urls)
        if len(urls) <= prev_n:
            break

    print(f"  [{idx}] -> {len(urls)} images")
    return _SlotResult(
        idx=idx,
        label=label,
        keyword=keyword,
        slot_kind=slot_kind,
        urls=urls,
        sources=sources,
        refill_round=refill_round,
        visual_accepted=visual_accepted,
        flyai_transport_only=flyai_transport_only,
        attempted_fill=attempted_fill,
    )


def main() -> int:
    alt_min, alt_max = resolved_alternate_bounds()
    parser = argparse.ArgumentParser(description="Fill roadbook image slots from Xiaohongshu.")
    parser.add_argument("trip_data", help="Path to tripData.json")
    parser.add_argument("--output", help="Output JSON path; default overwrites input")
    parser.add_argument("--html", help="Regenerate HTML path after updating JSON")
    parser.add_argument("--template", default="roadbook-v2", help="Template alias for regeneration")
    parser.add_argument(
        "--min-images",
        type=int,
        default=alt_min,
        help="每槽至少写入的备选 URL 数（默认来自仓库约定或环境变量 ROADBOOK_V2_IMAGE_ALTERNATES*）",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=alt_max,
        help="每槽最多保留的备选 URL 数（默认与 min 同源，可被 ROADBOOK_V2_IMAGE_ALTERNATES_MAX 单独覆盖）",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=240000,
        help="单次 MCP 调用超时（毫秒）。交付默认 240000（4 分钟）；过短易误判失败。",
    )
    parser.add_argument("--limit-slots", type=int, default=0, help="Only process first N slots, useful for smoke tests")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="已有足够 URL 的槽位跳过搜图（仅供本地提速；roadbook-v2 交付禁止使用，否则图文与行程可能错位）",
    )
    parser.add_argument(
        "--require-remote-urls",
        action="store_true",
        help="仅用本轮小红书 MCP 返回的 https 图写槽；不足则不合并本地 roadbook-images，结束时若有槽不达标则退出码 1（交付默认由 deliver 打开）",
    )
    parser.add_argument(
        "--no-image-fallback-chain",
        action="store_true",
        help="关闭「飞猪→Commons→占位」补足；与 --require-remote-urls 联用时小红书不足即判失败",
    )
    parser.add_argument(
        "--flyai-timeout",
        type=int,
        default=55,
        help="降级链中 flyai search-hotels/search-poi 子进程超时秒数（默认 55）",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="跳过 search_feeds 预检（仅供调试）",
    )
    parser.add_argument(
        "--skip-xhs",
        action="store_true",
        help="完全跳过小红书 MCP（不调 ensure_login / preflight / search_feeds），单槽仅走 flyai → Wikimedia Commons → placeholder 兜底链；供 deliver 在 MCP 挂掉后自动二次降级调用。与 --no-image-fallback-chain 互斥。",
    )
    parser.add_argument(
        "--slot-retries",
        type=int,
        default=3,
        help="单槽 search_feeds+详情 失败时的重试次数（默认 3）",
    )
    parser.add_argument(
        "--xhs-cooldown-ms",
        type=int,
        default=-1,
        help=(
            "每次 MCP 调用（search_feeds / get_feed_detail / 预检 / check_login_status）之后的等待毫秒数，缓解有头模式下频繁弹浏览器。"
            "未传时使用环境变量 ROADBOOK_FILL_XHS_COOLDOWN_MS，均无则 0。"
        ),
    )
    parser.add_argument(
        "--xhs-slot-gap-ms",
        type=int,
        default=-1,
        help=(
            "每处理完一个曾调用小红书取图的配图槽之后的额外间隔（毫秒）。未传时使用 ROADBOOK_FILL_XHS_SLOT_GAP_MS，均无则 0。"
        ),
    )
    parser.add_argument(
        "--xhs-max-detail-feeds",
        type=int,
        default=-1,
        help=(
            "单槽最多拉几条笔记详情（每条一次 get_feed_detail）。压低可减少请求与窗口抖动。默认 6；未传时使用 ROADBOOK_FILL_XHS_MAX_DETAIL_FEEDS。"
        ),
    )
    parser.add_argument(
        "--max-images-per-feed",
        type=int,
        default=-1,
        help="单篇小红书笔记最多采纳几张图（默认 5；环境 ROADBOOK_FILL_XHS_MAX_IMAGES_PER_FEED）。",
    )
    parser.add_argument(
        "--max-images-per-keyword-attempt",
        type=int,
        default=-1,
        help="每个关键词轮询最多为本槽新增几张（默认 2；环境 ROADBOOK_FILL_XHS_MAX_IMAGES_PER_KEYWORD）。",
    )
    parser.add_argument(
        "--visual-dedupe",
        action="store_true",
        help="启用感知哈希跨槽去重（需 pip install -r requirements-roadbook-images.txt）；或环境 ROADBOOK_FILL_VISUAL_DEDUPE=1",
    )
    parser.add_argument(
        "--xhs-concurrency",
        type=int,
        default=-1,
        help=(
            "TikHub 取图并发线程数（默认 4；环境 ROADBOOK_FILL_XHS_CONCURRENCY；"
            "传 1 退回串行。与 --visual-dedupe 互斥，互斥时自动回退 1。)"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    trip_path = Path(args.trip_data).resolve()
    out_path = Path(args.output).resolve() if args.output else trip_path

    if args.min_images < 1 or args.max_images < args.min_images:
        raise SystemExit("--max-images must be >= --min-images >= 1")

    if args.skip_xhs and args.no_image_fallback_chain:
        raise SystemExit("--skip-xhs 与 --no-image-fallback-chain 互斥：跳过小红书后必须启用 fallback 链")

    xhs_cooldown_ms = resolve_throttle_ms(
        args.xhs_cooldown_ms, "ROADBOOK_FILL_XHS_COOLDOWN_MS", 0
    )
    xhs_slot_gap_ms = resolve_throttle_ms(
        args.xhs_slot_gap_ms, "ROADBOOK_FILL_XHS_SLOT_GAP_MS", 0
    )
    xhs_max_feeds = resolve_throttle_ms(
        args.xhs_max_detail_feeds, "ROADBOOK_FILL_XHS_MAX_DETAIL_FEEDS", 6
    )
    xhs_max_feeds = max(1, min(int(xhs_max_feeds), 24))
    per_feed_cap = resolve_positive_int(
        args.max_images_per_feed,
        "ROADBOOK_FILL_XHS_MAX_IMAGES_PER_FEED",
        5,
    )
    kw_cap = resolve_positive_int(
        args.max_images_per_keyword_attempt,
        "ROADBOOK_FILL_XHS_MAX_IMAGES_PER_KEYWORD",
        2,
    )
    kw_cap = min(kw_cap, args.max_images)

    visual_dedupe = args.visual_dedupe or (
        os.environ.get("ROADBOOK_FILL_VISUAL_DEDUPE", "").strip().lower() in ("1", "true", "yes", "on")
    )

    from repo_dotenv import load_repo_dotenv
    from tikhub_xhs_cache import XhsNoteCache, cache_path_for_trip, set_active_cache
    from tikhub_xhs_feeds import ensure_tikhub_api_key, preflight_tikhub_search

    load_repo_dotenv(root)

    xhs_cache: XhsNoteCache | None = None
    if not args.skip_xhs:
        xhs_cache = XhsNoteCache(cache_path_for_trip(out_path))
        set_active_cache(xhs_cache)

    if args.skip_xhs:
        print("[skip-xhs] 跳过 TikHub 小红书，仅走 flyai → Commons → placeholder 兜底链", flush=True)
    else:
        if xhs_cooldown_ms or xhs_slot_gap_ms:
            print(
                f"[xhs-throttle] cooldown_ms={xhs_cooldown_ms} slot_gap_ms={xhs_slot_gap_ms} max_detail_feeds={xhs_max_feeds}",
                flush=True,
            )
        print("[xhs] 配图后端: TikHub API（TIKHUB_API_KEY）", flush=True)
        ensure_tikhub_api_key(root)
        if not args.no_preflight:
            print("[preflight] tikhub search_notes …", flush=True)
            preflight_tikhub_search(
                root,
                args.timeout_ms,
                cooldown_ms=xhs_cooldown_ms,
                throttle_sleep_ms=throttle_sleep_ms,
            )
            print("[preflight] OK", flush=True)

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    entries = collect_image_entries(data)
    if args.limit_slots:
        entries = entries[: args.limit_slots]

    cross_slot = os.environ.get(
        "ROADBOOK_FILL_XHS_NO_CROSS_SLOT_DEDUPE",
        "",
    ).strip().lower() not in ("1", "true", "yes", "on")

    global_used: set[str] = set()
    if cross_slot:
        for _p, ent in entries:
            register_url_fingerprints(existing_urls(ent), global_used)

    vcache = None
    book_phash: set[str] = set()
    if visual_dedupe:
        from roadbook_image_engine.cache_store import ImageMetaCache

        vcache = ImageMetaCache(repo_root=root)
        for _p, ent in entries:
            for u in existing_urls(ent):
                nu = normalize_xhs_image_url(u)
                if not is_remote_https_image_url(nu):
                    continue
                row = vcache.get_by_url(nu)
                if row and row.get("phash"):
                    book_phash.add(str(row["phash"]))

    report: list[dict[str, Any]] = []
    changed = 0
    failed_strict: list[str] = []

    jobs: list[_SlotJob] = []
    for idx, (path, entry) in enumerate(entries, 1):
        label = entry.get("slotLabel", "")
        current_urls = existing_urls(entry)
        current_fps: set[str] = set()
        register_url_fingerprints(current_urls, current_fps)
        jobs.append(
            _SlotJob(
                idx=idx,
                path=path,
                entry=entry,
                label=label,
                current_urls=current_urls,
                current_fps=current_fps,
            )
        )

    xhs_concurrency_default = resolve_positive_int(
        args.xhs_concurrency, "ROADBOOK_FILL_XHS_CONCURRENCY", 4
    )
    xhs_concurrency = max(1, min(16, xhs_concurrency_default))
    if visual_dedupe and xhs_concurrency > 1:
        print(
            "[xhs-concurrency] --visual-dedupe 与并发互斥（SQLite phash 缓存非多线程安全）；自动回退 concurrency=1",
            flush=True,
        )
        xhs_concurrency = 1
    if args.skip_xhs and xhs_concurrency > 1:
        print("[xhs-concurrency] --skip-xhs 模式无 TikHub 调用，concurrency=1 即可", flush=True)
        xhs_concurrency = 1
    print(f"[xhs-concurrency] worker={xhs_concurrency}", flush=True)

    initial_global_used = set(global_used)
    initial_book_phash = set(book_phash)

    def _build_kwargs(job: _SlotJob, *, exclude_override: set[str] | None = None) -> dict[str, Any]:
        if exclude_override is not None:
            slot_exclude = (exclude_override - job.current_fps) if cross_slot else None
        else:
            slot_exclude = (initial_global_used - job.current_fps) if cross_slot else None
        slot_phash_allow = (
            collect_phash_allowlist_from_urls(vcache, job.current_urls) if vcache else set()
        )
        return dict(
            job=job,
            total=len(jobs),
            data=data,
            args=args,
            root=root,
            trip_path=trip_path,
            xhs_cooldown_ms=xhs_cooldown_ms,
            xhs_max_feeds=xhs_max_feeds,
            per_feed_cap=per_feed_cap,
            kw_cap=kw_cap,
            slot_exclude=slot_exclude,
            slot_phash_allow=slot_phash_allow,
            book_phash_snapshot=initial_book_phash,
            vcache=vcache,
            visual_dedupe=visual_dedupe,
        )

    results: list[_SlotResult | None] = [None] * len(jobs)

    def _apply_skip_slot(job: _SlotJob, r: _SlotResult) -> None:
        """处理 brand-logo / fee-service / skip-existing 三类跳过槽（写 data 与 report）。"""
        if r.skip_fixed_brand_logo:
            if not args.dry_run:
                img = get_by_path(data, job.path)
                if isinstance(img, dict):
                    img["url"] = brand_logo_relative_url()
                    img["alternates"] = []
                    img["slotLabel"] = (
                        job.label if isinstance(job.label, str) and job.label.strip() else "品牌 LOGO"
                    )
            report.append(
                {
                    "path": "/".join(map(str, job.path)),
                    "slotLabel": job.label,
                    "keyword": "",
                    "count": 1,
                    "sources": [],
                    "strict_failed": False,
                    "skip_fixed_brand_logo": True,
                }
            )
            print(f"  [{job.idx}] -> {brand_logo_relative_url()} (fixed)", flush=True)
            return
        if r.skip_empty_hotel_feature:
            if not args.dry_run:
                img = get_by_path(data, job.path)
                if isinstance(img, dict):
                    img["alternates"] = []
                    img.pop("url", None)
            report.append(
                {
                    "path": "/".join(map(str, job.path)),
                    "slotLabel": job.label,
                    "keyword": "",
                    "count": 0,
                    "sources": [],
                    "strict_failed": False,
                    "skip_empty_hotel_feature": True,
                }
            )
            print(f"  [{job.idx}] -> 0 images (skipped)", flush=True)
            return
        if r.skip_fee_service_text_block:
            if not args.dry_run:
                img = get_by_path(data, job.path)
                if isinstance(img, dict):
                    img["alternates"] = []
                    img.pop("url", None)
                    if not isinstance(img.get("slotLabel"), str):
                        img["slotLabel"] = job.label
            report.append(
                {
                    "path": "/".join(map(str, job.path)),
                    "slotLabel": job.label,
                    "keyword": "",
                    "count": 0,
                    "sources": [],
                    "strict_failed": False,
                    "skip_fee_service_text_block": True,
                }
            )
            return
        # skip_existing：保留 entry 原值，写 report 即可
        report.append(
            {
                "path": "/".join(map(str, job.path)),
                "slotLabel": job.label,
                "keyword": "",
                "count": len(job.current_urls),
                "sources": [],
                "strict_failed": False,
                "skip_existing": True,
            }
        )

    def _commit_slot(job: _SlotJob, r: _SlotResult, *, write_report: bool = True) -> bool:
        """合并阶段：前向去重 + 写回 data + 累积 global_used / book_phash；返回是否达标。

        ``write_report=False`` 时仅更新内部状态、不写 report，留给补漏阶段统一处理。
        """
        nonlocal changed
        urls = list(r.urls or [])
        if cross_slot:
            slot_exclude = global_used - job.current_fps
            urls = dedupe_urls_by_fingerprint(urls, exclude_fingerprints=slot_exclude)
        if args.require_remote_urls:
            urls = [u for u in urls if is_remote_https_image_url(u)]
        urls = dedupe(urls)[: args.max_images]
        r.urls = urls

        meets = len(urls) >= args.min_images
        if meets and not args.dry_run:
            set_by_path(data, job.path, {"alternates": urls[: args.max_images], "slotLabel": job.label})
            if cross_slot:
                for fp in job.current_fps:
                    global_used.discard(fp)
                register_url_fingerprints(urls[: args.max_images], global_used)
                job.current_fps = set()
                register_url_fingerprints(urls[: args.max_images], job.current_fps)
            if vcache:
                slot_allow = collect_phash_allowlist_from_urls(vcache, job.current_urls)
                for ph in slot_allow:
                    book_phash.discard(ph)
                for ph in r.visual_accepted:
                    book_phash.add(ph)
            changed += 1

        if write_report:
            entry = {
                "path": "/".join(map(str, job.path)),
                "slotLabel": job.label,
                "keyword": r.keyword,
                "slot_kind": r.slot_kind,
                "count": len(urls),
                "sources": r.sources,
                "strict_failed": False,
                "visual_dedupe": bool(visual_dedupe),
                "visual_dedupe_refill_rounds": r.refill_round,
                "max_images_per_feed": per_feed_cap,
                "max_images_per_keyword_attempt": kw_cap,
            }
            if r.error:
                entry["error"] = r.error
            if not meets and args.require_remote_urls:
                print(
                    f"  [{job.idx}] strict: only {len(urls)} remote URL(s), slot not updated",
                    file=sys.stderr,
                )
                failed_strict.append(job.label)
                entry["strict_failed"] = True
            report.append(entry)
        return meets

    pending_refill: list[_SlotJob] = []

    if xhs_concurrency <= 1:
        for job in jobs:
            try:
                r = _process_slot(**_build_kwargs(job, exclude_override=global_used))
            except Exception as exc:  # noqa: BLE001
                print(f"WARN [{job.idx}/{len(jobs)}] worker failed: {exc}", file=sys.stderr)
                r = _SlotResult(
                    idx=job.idx, label=job.label, keyword="", slot_kind="", error=str(exc),
                )
            results[job.idx - 1] = r
            if (
                r.skip_fixed_brand_logo
                or r.skip_empty_hotel_feature
                or r.skip_fee_service_text_block
                or r.skip_existing
            ):
                _apply_skip_slot(job, r)
                continue
            _commit_slot(job, r)
            if (
                r.attempted_fill
                and not args.skip_xhs
                and xhs_slot_gap_ms > 0
                and job.idx < len(jobs)
            ):
                throttle_sleep_ms(xhs_slot_gap_ms)
    else:
        with ThreadPoolExecutor(
            max_workers=xhs_concurrency, thread_name_prefix="xhs-slot"
        ) as pool:
            future_to_job = {
                pool.submit(_process_slot, **_build_kwargs(job)): job for job in jobs
            }
            for fut in as_completed(future_to_job):
                job = future_to_job[fut]
                try:
                    results[job.idx - 1] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"WARN [{job.idx}/{len(jobs)}] worker failed: {exc}",
                        file=sys.stderr,
                    )
                    results[job.idx - 1] = _SlotResult(
                        idx=job.idx, label=job.label, keyword="", slot_kind="",
                        error=str(exc),
                    )

        for job in jobs:
            r = results[job.idx - 1]
            if r is None:
                continue
            if (
                r.skip_fixed_brand_logo
                or r.skip_empty_hotel_feature
                or r.skip_fee_service_text_block
                or r.skip_existing
            ):
                _apply_skip_slot(job, r)
                continue
            if r.error:
                _commit_slot(job, r)
                continue
            meets = _commit_slot(job, r, write_report=False)
            if not meets:
                pending_refill.append(job)
            else:
                _commit_slot(job, r)

    if pending_refill:
        print(
            f"[xhs-refill] {len(pending_refill)} 个槽前向去重后不足，串行补一轮…",
            flush=True,
        )
        for job in pending_refill:
            try:
                r2 = _process_slot(**_build_kwargs(job, exclude_override=global_used))
            except Exception as exc:  # noqa: BLE001
                print(f"WARN [{job.idx}] refill failed: {exc}", file=sys.stderr)
                r2 = results[job.idx - 1] or _SlotResult(
                    idx=job.idx, label=job.label, keyword="", slot_kind="", error=str(exc),
                )
            else:
                prev = results[job.idx - 1]
                if prev is not None:
                    r2.urls = dedupe(list(prev.urls or []) + list(r2.urls or []))
                    if not r2.sources:
                        r2.sources = prev.sources
            results[job.idx - 1] = r2
            _commit_slot(job, r2)

    if args.require_remote_urls and failed_strict:
        print(
            f"ERROR: --require-remote-urls 下 {len(failed_strict)} 个槽未从小红书凑满 {args.min_images} 张 https 图",
            file=sys.stderr,
        )
        for lb in failed_strict[:15]:
            print(f"  - {lb}", file=sys.stderr)
        if len(failed_strict) > 15:
            print(f"  … 另有 {len(failed_strict) - 15} 个", file=sys.stderr)

    if xhs_cache is not None:
        try:
            xhs_cache.save()
            print(f"[xhs-cache] {xhs_cache.stats_line()} → {xhs_cache.path}", flush=True)
        except OSError as exc:
            print(f"WARN xhs-cache save failed: {exc}", file=sys.stderr)

    if not args.dry_run:
        data.setdefault("meta", {})["updatedAt"] = datetime.now(timezone.utc).isoformat()
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        sources_dir = out_path.parent / "sources"
        sources_dir.mkdir(exist_ok=True)
        (sources_dir / "xiaohongshu-image-sources.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if args.html:
            run(
                [
                    sys.executable,
                    str(root / "assets" / "generate.py"),
                    str(out_path),
                    str(Path(args.html).resolve()),
                    "--template",
                    args.template,
                    "--no-serve",
                    "--no-open",
                ],
                cwd=root,
                timeout=120,
            )

    print(f"Done. updated_slots={changed}, total_slots={len(entries)}")
    if args.require_remote_urls and failed_strict:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
