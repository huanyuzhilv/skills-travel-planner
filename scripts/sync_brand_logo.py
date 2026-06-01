#!/usr/bin/env python3
"""更新 ``tripData.cover.logo`` 为品牌 Logo 逻辑路径（不复制本地文件；可选后端转存对象存储）。

由 ``fill_xhs_images.py`` 在交付流水线中写入同一占位路径；本脚本仅供手工补写 JSON：

    python3 scripts/sync_brand_logo.py path/to/tripData.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS.parent

from brand_logo import patch_cover_logo_in_trip_data  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Patch cover.logo brand placeholder in tripData (optional backend OSS upload)."
    )
    ap.add_argument("trip_json", help="Path to tripData.json")
    args = ap.parse_args()

    trip_path = Path(args.trip_json).resolve()
    data = json.loads(trip_path.read_text(encoding="utf-8"))
    patch_cover_logo_in_trip_data(data)
    meta = data.setdefault("meta", {})
    meta["updatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    trip_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("sync_brand_logo: OK → cover.logo", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
