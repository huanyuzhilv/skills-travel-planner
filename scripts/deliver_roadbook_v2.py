#!/usr/bin/env python3
"""Roadbook v2 标准交付流水线（单一入口，参数写死）。

顺序固定：
  0. （可选）merge_intake_fee_service.py — `--intake-brief` 传入简表 txt/md 时，把费用/服务相关段落写入 text-cost-001 / text-service-001
  1. enrich_daily_descriptions_from_xhs.py — 每日行程正文（小红书 → 飞猪 POI → 维基兜底，默认 ≥ daily-min-chars）；离线草稿见 `--allow-local-placeholders`
  2. enrich_roadbook_copy_from_llm.py — 行程亮点 + 费用/服务说明（OPENAI 兼容 LLM，对齐 SKILL.md）
  3. enrich_hotel_intro_from_flyai.py — 住宿长简介（飞猪）
  4. fill_xhs_images.py — 预检 + 按槽搜图（TikHub API；交通固定飞猪）；封面 Logo、费用/服务不搜图；默认 --require-remote-urls
  5. validate_roadbook_image_alternates.py — 每槽 URL ≥ --min（封面 Logo、费用/服务配图槽不参与校验）
  6. assets/generate.py — 渲染 HTML（--no-serve --no-open --no-localize-images；配图保持 https）

用途：团队/skill 使用者无需记得「还要单独发指令跑补图」，保证交付质量一致。

依赖：python3、**TikHub**（``.env`` 中 ``TIKHUB_API_KEY``，配图与每日正文 enrich）、**可选 ``OPENAI_API_KEY`` / ``DEEPSEEK_API_KEY``**、flyai（住宿步骤）。

**默认每次执行（无须手抄）**：住宿步骤默认 **`--force`**；每日正文 enrich 默认 **`--force` 润色**（劣质/拼接稿自动重写；无 LLM 时用 overview+洁净素材合成顾问文风）。加 **`--no-hotel-force`** / **`--no-daily-force`** 可保留已达字数的旧稿。每日仍跑完整链（小红书 → 飞猪 POI → 维基，≥ ``--daily-min-chars``）。

**TikHub 负载**：步骤 1（每日 enrich）与步骤 4（补图）均按次计费。缓解：设置 ``ROADBOOK_FILL_XHS_COOLDOWN_MS`` 在请求间休眠；若只需稳定出图、可暂跳每日正文：``--skip-daily-enrich``。

用法:
  cd "[skills-travel-planner 仓库根]"
  python3 scripts/deliver_roadbook_v2.py \\
    "某路书目录/tripData.json" \\
    "某路书目录/路书名.html" \\
    --check-in YYYY-MM-DD --check-out YYYY-MM-DD
  # 住宿 --force 与简介模板阈值每次默认执行；仅当需保留已有住宿长文时：--no-hotel-force
  # 可选：在同命令末尾增加 --intake-brief "某路书目录/sources/brief.txt"

调试（允许本地占位、且不逐个槽呼叫小红书 MCP）加：--allow-local-placeholders

**默认容错（降级）**：小红书每日正文、住宿飞猪、strict 补图或校验任一失败时 **不中止整条流水线**——打印 WARN。strict 补图失败时按顺序兜底：
① ``fill_xhs_images.py --skip-xhs``（跳过小红书 MCP，单槽仅走 **飞猪 FlyAI → Wikimedia Commons → placehold.co 占位**）；② 仍失败则跳 ``relink_local_roadbook_images.py`` 用本地图回链；③ 校验改为放宽（先去掉 https 门禁，仍不满再 ``--min 1``）；**仍执行 ``generate.py``**。  
若须恢复「一步失败即退出」，请加 **`--fail-fast`**。

**退出码语义**：
- ``0``：strict 交付成功（每槽 ≥ ``--min-images`` 张 https URL，可直接交付客户）。
- ``2``：流水线完成但**已降级**（任一项触发本地回链 / 放宽校验）。stdout 末行以 ``DEGRADED:`` 起头并标注降级原因——**禁止当作 strict 交付稿直接发给客户**，须人工核对配图与正文。
- 其它非 0：``--fail-fast`` 下任一步失败、或参数 / IO 异常。

每槽备选图数量默认为 **4**（``--min-images`` / ``--max-images``），也可用环境变量 ``ROADBOOK_V2_IMAGE_ALTERNATES`` 或 ``ROADBOOK_V2_IMAGE_ALTERNATES_MIN`` / ``_MAX`` 覆盖默认值。

**并发提速**：fill_xhs_images 默认开启 **4 线程**（``ROADBOOK_FILL_XHS_CONCURRENCY``），共享 keep-alive ``httpx.Client``。
通常可让 fill 阶段提速 3–5×。需禁用并发设 ``ROADBOOK_FILL_XHS_CONCURRENCY=1``。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from repo_dotenv import load_repo_dotenv
from roadbook_image_alternate_defaults import resolved_alternate_bounds

try:
    from enrich_hotel_intro_from_flyai import (
        DEFAULT_FORCED_REF_SUBSTANCE_MAX,
        DEFAULT_WEB_SHORT_CHARS,
    )
except ImportError:  # 极小环境仅拷贝单文件时的兜底
    DEFAULT_FORCED_REF_SUBSTANCE_MAX = 168
    DEFAULT_WEB_SHORT_CHARS = 120


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_travel_dates(check_in: str, check_out: str, trip: Path) -> tuple[str, str]:
    """未传或只传一侧时，从 tripData.meta 或 generationDate 补全；不做格式/先后校验。"""
    ci = (check_in or "").strip()
    co = (check_out or "").strip()
    if ci and co:
        return ci, co

    try:
        data = json.loads(trip.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}

    if not ci:
        ci = str(meta.get("travelCheckIn") or "").strip()
    if not co:
        co = str(meta.get("travelCheckOut") or "").strip()
    if ci and co:
        return ci, co

    base = str(meta.get("generationDate") or meta.get("updatedAt") or "")[:10]
    try:
        dt = datetime.strptime(base, "%Y-%m-%d")
    except ValueError:
        dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if not ci:
        ci = dt.strftime("%Y-%m-%d")
    if not co:
        co = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return ci, co


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _run_allow_fail(cmd: list[str], *, cwd: Path, label: str) -> bool:
    """退出码非零时返回 False，不抛异常。"""
    print("+", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd))
    if proc.returncode != 0:
        print(
            f"WARN [{label}] 退出码 {proc.returncode}；默认继续降级流水线（避免中止）。"
            f"若需遇错即停请加 --fail-fast。",
            flush=True,
        )
        return False
    return True


def _persist_hotel_feature_module(trip: Path) -> None:
    """保证 tripData 含「住宿安排」模块；无酒店或未匹配时为空卡片。"""
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from roadbook_intake import ensure_hotel_feature_module  # noqa: PLC0415

    data = json.loads(trip.read_text(encoding="utf-8"))
    if ensure_hotel_feature_module(data):
        trip.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("+ 已补全空白「住宿安排」模块 → tripData", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Roadbook v2: daily XHS copy → hotel → XHS images → validate → generate HTML.",
    )
    alt_min, alt_max = resolved_alternate_bounds()
    ap.add_argument("trip_json", help="Path to tripData.json")
    ap.add_argument("output_html", help="Output HTML path")
    ap.add_argument(
        "--check-in",
        default="",
        help="首晚入住日 YYYY-MM-DD（可选；未传时从 tripData.meta 或 generationDate 推算）",
    )
    ap.add_argument(
        "--check-out",
        default="",
        help="离店日 YYYY-MM-DD（可选；未传时与入住日相差 1 天）",
    )
    ap.add_argument("--min-chars", type=int, default=200, help="住宿简介最短字数（默认 200）")
    ap.add_argument(
        "--hotel-force",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="默认开启：含备选/拟定酒店清单时强制重写住宿简介（传 enrich --force）；--no-hotel-force 保留已达字数的成稿",
    )
    ap.add_argument(
        "--skip-hotel-enrich",
        action="store_true",
        help="跳过住宿简介步骤（仅无住宿卡片或纯调试时使用）",
    )
    ap.add_argument(
        "--skip-daily-enrich",
        action="store_true",
        help="跳过每日 description 小红书文案步骤",
    )
    ap.add_argument(
        "--no-daily-force",
        action="store_true",
        help="每日正文 enrich 不强制重写（默认每次 deliver 均 --force 润色）",
    )
    ap.add_argument(
        "--daily-force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--daily-min-chars",
        type=int,
        default=200,
        help="每日描述最短字数目标（默认 200，传给 enrich_daily_descriptions_from_xhs.py）",
    )
    ap.add_argument(
        "--daily-no-llm",
        action="store_true",
        help="传给 enrich_daily：禁用 LLM 统一文风（仅用顾问合成，不需 OPENAI_API_KEY / DEEPSEEK_API_KEY）",
    )
    ap.add_argument(
        "--skip-copy-llm",
        action="store_true",
        help="跳过行程亮点/费用/服务 LLM 润色（enrich_roadbook_copy_from_llm）",
    )
    ap.add_argument(
        "--no-copy-llm-force",
        action="store_true",
        help="亮点/费用/服务已达字数时不强制重写（默认 deliver 传 --force）",
    )
    ap.add_argument(
        "--copy-llm-no-llm",
        action="store_true",
        help="传给 enrich_roadbook_copy_from_llm：禁用 LLM",
    )
    ap.add_argument(
        "--min-images",
        type=int,
        default=alt_min,
        help="每槽最少备选 URL（默认 4，可由 ROADBOOK_V2_IMAGE_ALTERNATES* 环境变量覆盖）",
    )
    ap.add_argument(
        "--max-images",
        type=int,
        default=alt_max,
        help="每槽最多备选 URL（默认与 min 一致，可用 _MAX 环境变量单独覆盖）",
    )
    ap.add_argument(
        "--validate-min",
        type=int,
        default=None,
        help="校验脚本 --min（默认与 --min-images 相同）",
    )
    ap.add_argument(
        "--timeout-ms",
        type=int,
        default=240000,
        help="小红书 MCP 单次调用超时（毫秒）；交付默认 240000（4 分钟）",
    )
    ap.add_argument(
        "--intake-brief",
        default=None,
        metavar="PATH",
        help="可选：行程/报价简表 (.txt/.md)，合并费用说明与服务说明正文到现有 tripData（规则同 roadbook_intake）",
    )
    ap.add_argument("--template", default="roadbook-v2", help="generate.py --template")
    ap.add_argument(
        "--allow-local-placeholders",
        action="store_true",
        help="跳过小红书 fill_xhs（避免 MCP 卡死）；校验仅数 URL 数量；用于离线/内部草稿",
    )
    ap.add_argument(
        "--fail-fast",
        action="store_true",
        help="任一步骤（每日 enrich / 住宿 enrich / fill_xhs / validate）非零退出码即中止；默认关闭以便异常后降级仍生成 HTML",
    )
    ap.add_argument(
        "--hotel-no-forced-reference",
        action="store_true",
        help="传给 enrich_hotel：禁用「简介」模板（--no-forced-reference）",
    )
    ap.add_argument(
        "--hotel-forced-ref-substance-max",
        type=int,
        default=DEFAULT_FORCED_REF_SUBSTANCE_MAX,
        metavar="N",
        help="传给 enrich_hotel：飞猪素材低于 N 字则强加简介（默认与 enrich 脚本一致）",
    )
    ap.add_argument(
        "--hotel-web-short-chars",
        type=int,
        default=DEFAULT_WEB_SHORT_CHARS,
        metavar="N",
        help="传给 enrich_hotel：无飞猪时网络摘录短于此则简介为主（默认与 enrich 脚本一致）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印流水线各步骤命令，不实际执行",
    )
    args = ap.parse_args()

    root = _root()
    load_repo_dotenv(root)

    py = sys.executable
    trip = Path(args.trip_json).resolve()
    html_out = Path(args.output_html).resolve()
    dry_run = args.dry_run

    check_in, check_out = _resolve_travel_dates(args.check_in, args.check_out, trip)
    if not args.check_in.strip() or not args.check_out.strip():
        print(f"+ 入住/离店（自动补全）: {check_in} → {check_out}", flush=True)

    # 将出行日期写入 tripData.meta，供下游关键词生成做季节匹配
    if not dry_run:
        _trip_data = json.loads(trip.read_text(encoding="utf-8"))
        _meta = _trip_data.setdefault("meta", {})
        _meta["travelCheckIn"] = check_in
        _meta["travelCheckOut"] = check_out
        trip.write_text(json.dumps(_trip_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not dry_run:
        _persist_hotel_feature_module(trip)

    if args.min_chars < 200:
        print("WARNING: --min-chars 低于 200 不符合交付默认", file=sys.stderr)

    fail_fast = args.fail_fast

    if args.intake_brief:
        brief = Path(args.intake_brief).resolve()
        cmd = [
            py,
            str(root / "scripts" / "merge_intake_fee_service.py"),
            str(trip),
            str(brief),
        ]
        if dry_run:
            print(f"+ [dry-run] {' '.join(cmd)}", flush=True)
        elif fail_fast:
            _run(cmd, cwd=root)
        else:
            _run_allow_fail(cmd, cwd=root, label="merge_intake_fee_service")

    if not args.skip_daily_enrich and not args.allow_local_placeholders:
        daily_cmd = [
            py,
            str(root / "scripts" / "enrich_daily_descriptions_from_xhs.py"),
            str(trip),
            "--timeout-ms",
            str(args.timeout_ms),
            "--min-chars",
            str(args.daily_min_chars),
        ]
        if not args.no_daily_force or args.daily_force:
            daily_cmd.append("--force")
        if args.daily_no_llm:
            daily_cmd.append("--no-llm")
        if dry_run:
            print(f"+ [dry-run] {' '.join(daily_cmd)}", flush=True)
        elif fail_fast:
            _run(daily_cmd, cwd=root)
        else:
            _run_allow_fail(daily_cmd, cwd=root, label="enrich_daily_descriptions_from_xhs")
    elif args.allow_local_placeholders:
        print("+ skip enrich_daily_descriptions_from_xhs (--allow-local-placeholders)", flush=True)
    elif dry_run:
        print("+ [dry-run] skip enrich_daily_descriptions_from_xhs (--skip-daily-enrich)", flush=True)

    if not args.skip_copy_llm and not args.allow_local_placeholders:
        copy_cmd = [
            py,
            str(root / "scripts" / "enrich_roadbook_copy_from_llm.py"),
            str(trip),
        ]
        if not args.no_copy_llm_force:
            copy_cmd.append("--force")
        if args.copy_llm_no_llm:
            copy_cmd.append("--no-llm")
        if args.intake_brief:
            copy_cmd.extend(["--brief", str(Path(args.intake_brief).resolve())])
        if dry_run:
            print(f"+ [dry-run] {' '.join(copy_cmd)}", flush=True)
        elif fail_fast:
            _run(copy_cmd, cwd=root)
        else:
            _run_allow_fail(copy_cmd, cwd=root, label="enrich_roadbook_copy_from_llm")
    elif args.allow_local_placeholders:
        print("+ skip enrich_roadbook_copy_from_llm (--allow-local-placeholders)", flush=True)
    elif args.skip_copy_llm:
        print("+ skip enrich_roadbook_copy_from_llm (--skip-copy-llm)", flush=True)
    elif dry_run:
        print("+ [dry-run] skip enrich_roadbook_copy_from_llm", flush=True)

    if not args.skip_hotel_enrich and not args.allow_local_placeholders:
        enrich_cmd = [
            py,
            str(root / "scripts" / "enrich_hotel_intro_from_flyai.py"),
            str(trip),
            "--check-in",
            check_in,
            "--check-out",
            check_out,
            "--min-chars",
            str(args.min_chars),
        ]
        if args.hotel_force:
            enrich_cmd.append("--force")
        if args.hotel_no_forced_reference:
            enrich_cmd.append("--no-forced-reference")
        enrich_cmd.extend(
            [
                "--forced-ref-substance-max",
                str(args.hotel_forced_ref_substance_max),
                "--web-short-chars",
                str(args.hotel_web_short_chars),
            ]
        )
        if dry_run:
            print(f"+ [dry-run] {' '.join(enrich_cmd)}", flush=True)
        elif fail_fast:
            _run(enrich_cmd, cwd=root)
        else:
            _run_allow_fail(enrich_cmd, cwd=root, label="enrich_hotel_intro_from_flyai")
    elif args.allow_local_placeholders:
        print("+ skip enrich_hotel_intro_from_flyai (--allow-local-placeholders)", flush=True)
    elif dry_run and args.skip_hotel_enrich:
        print("+ [dry-run] skip enrich_hotel_intro_from_flyai (--skip-hotel-enrich)", flush=True)

    if not dry_run:
        _persist_hotel_feature_module(trip)

    degraded_fill = False
    degraded_skip_xhs = False
    # strict：小红书按槽搜图；本地占位草稿：跳过 fill（否则会逐个槽连 MCP 直至超时）
    if not args.allow_local_placeholders:
        fill_cmd = [
            py,
            str(root / "scripts" / "fill_xhs_images.py"),
            str(trip),
            "--min-images",
            str(args.min_images),
            "--max-images",
            str(args.max_images),
            "--timeout-ms",
            str(args.timeout_ms),
            "--require-remote-urls",
        ]
        if dry_run:
            print(f"+ [dry-run] {' '.join(fill_cmd)}", flush=True)
        elif fail_fast:
            _run(fill_cmd, cwd=root)
        else:
            ok_fill = _run_allow_fail(fill_cmd, cwd=root, label="fill_xhs_images (--require-remote-urls)")
            if not ok_fill:
                # 重试一次原始 fill_xhs（可能为临时网络故障）
                print("+ INFO [降级] fill_xhs_images 失败 → 重试一次 …", flush=True)
                ok_fill_retry = _run_allow_fail(fill_cmd, cwd=root, label="fill_xhs_images retry")
                if ok_fill_retry:
                    print("+ INFO 重试成功，继续 strict 路径", flush=True)
                else:
                    degraded_fill = True
                    # 降级 ①：完全跳过小红书 MCP，仅走 flyai → Commons → placeholder 兜底链
                    skip_xhs_cmd = [*fill_cmd, "--skip-xhs"]
                    print("+ INFO [降级] 重试仍失败 → 改走 --skip-xhs（flyai → Commons → placeholder）…", flush=True)
                    ok_skip_xhs = _run_allow_fail(skip_xhs_cmd, cwd=root, label="fill_xhs_images --skip-xhs")
                    if ok_skip_xhs:
                        degraded_skip_xhs = True
                    else:
                        # 降级 ②：flyai/Commons 也失败 → 本地图回链防裂图
                        relink_cmd = [py, str(root / "scripts" / "relink_local_roadbook_images.py"), str(trip)]
                        print("+ INFO [降级] --skip-xhs 仍失败 → 尝试 relink_local_roadbook_images …", flush=True)
                        subprocess.run(relink_cmd, cwd=str(root))
    else:
        print("+ skip fill_xhs_images (--allow-local-placeholders)", flush=True)

    vmin = args.validate_min if args.validate_min is not None else args.min_images
    validate_script = str(root / "scripts" / "validate_roadbook_image_alternates.py")
    generate_cmd = [
        py,
        str(root / "assets" / "generate.py"),
        str(trip),
        str(html_out),
        "--template",
        args.template,
        "--no-serve",
        "--no-open",
        "--no-localize-images",
    ]

    def _validate_once(extra: list[str], *, label: str) -> bool:
        cmd = [py, validate_script, str(trip), "--min", str(vmin), *extra]
        if dry_run:
            print(f"+ [dry-run] {' '.join(cmd)}", flush=True)
            return True
        if fail_fast:
            print("+", " ".join(cmd), flush=True)
            proc = subprocess.run(cmd, cwd=str(root))
            if proc.returncode != 0:
                print(f"交付流水线失败（{label} 退出码 {proc.returncode}）", file=sys.stderr)
                raise subprocess.CalledProcessError(proc.returncode or 1, cmd)
            return True
        return _run_allow_fail(cmd, cwd=root, label=label)

    want_https = not args.allow_local_placeholders and (not degraded_fill or degraded_skip_xhs)
    degraded_validate = False
    degraded_validate_min1 = False

    if dry_run:
        extras = ["--require-remote-urls"] if want_https else []
        _validate_once(extras, label="validate_roadbook_image_alternates")
    elif fail_fast:
        extras = ["--require-remote-urls"] if want_https else []
        _validate_once(extras, label="validate_roadbook_image_alternates")
    else:
        ok_v = _validate_once(
            ["--require-remote-urls"] if want_https else [],
            label="validate（strict 配图门禁）" if want_https else "validate（本地/混合）",
        )
        if not ok_v and want_https:
            print("+ INFO [降级] 校验去掉 https 门禁重试 …", flush=True)
            degraded_validate = True
            ok_v = _validate_once([], label="validate（无 require-remote-urls）")
        if not ok_v:
            print("+ INFO [降级] 校验改用每槽至少 1 张 URL …", flush=True)
            degraded_validate_min1 = True
            cmd_min1 = [py, validate_script, str(trip), "--min", "1"]
            print("+", " ".join(cmd_min1), flush=True)
            subprocess.run(cmd_min1, cwd=str(root))
            print(
                "WARN validate 在 --min 1 下仍可能失败；无论如何将继续 generate（请人工核对配图）。",
                flush=True,
            )

    if dry_run:
        print(f"+ [dry-run] {' '.join(generate_cmd)}", flush=True)
        print(f"[dry-run] 完成（未实际执行任何步骤）→ 目标 HTML: {html_out}", flush=True)
        return 0

    _run(generate_cmd, cwd=root)

    degraded = degraded_fill or degraded_validate or degraded_validate_min1
    if degraded:
        reasons: list[str] = []
        if degraded_fill and degraded_skip_xhs:
            reasons.append("strict 小红书补图失败 → flyai/Commons 兜底（--skip-xhs）")
        elif degraded_fill:
            reasons.append("strict 配图失败 → 本地回链")
        if degraded_validate:
            reasons.append("validate 去掉 https 门禁")
        if degraded_validate_min1:
            reasons.append("validate 放宽至 --min 1")
        tail = "（降级原因：" + "；".join(reasons) + "。非 strict 交付稿，禁止直接交付客户，请核对配图与正文）"
        print(f"DEGRADED: 交付流水线完成（已降级）→ {html_out}{tail}", flush=True)
        # 退出码 2：与 strict 成功（0）区分，调用方/Agent 可据此判断是否继续交付。
        return 2
    print(f"OK: 交付流水线完成（strict）→ {html_out}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"交付流水线失败（退出码 {exc.returncode}）", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
