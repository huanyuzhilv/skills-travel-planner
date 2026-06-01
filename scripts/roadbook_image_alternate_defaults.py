"""roadbook-v2 每槽备选图（URL）数量的默认值与环境变量覆盖。

交付链路（``fill_xhs_images`` / ``deliver_roadbook_v2`` / ``validate_roadbook_image_alternates``）
共用此处解析结果；**命令行参数优先级高于环境变量**。

环境变量（可选，均为正整数）：

- ``ROADBOOK_V2_IMAGE_ALTERNATES``：同时作为 ``--min-images`` 与 ``--max-images`` 的默认值。
- ``ROADBOOK_V2_IMAGE_ALTERNATES_MIN`` / ``ROADBOOK_V2_IMAGE_ALTERNATES_MAX``：分别覆盖最小 / 最大张数。

若只设置 min 且 max 未设置，max 仍沿用「二者共用」规则（见 ``resolved_alternate_bounds``）。
"""

from __future__ import annotations

import os

DEFAULT_IMAGE_ALTERNATES_MIN = 4
DEFAULT_IMAGE_ALTERNATES_MAX = 4


def _parse_positive_int(raw: str | None) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        n = int(str(raw).strip(), 10)
        return n if n >= 1 else None
    except ValueError:
        return None


def resolved_alternate_bounds() -> tuple[int, int]:
    """返回 (min_default, max_default)，供 argparse ``default=`` 使用。"""
    both = _parse_positive_int(os.environ.get("ROADBOOK_V2_IMAGE_ALTERNATES"))
    lo = _parse_positive_int(os.environ.get("ROADBOOK_V2_IMAGE_ALTERNATES_MIN"))
    hi = _parse_positive_int(os.environ.get("ROADBOOK_V2_IMAGE_ALTERNATES_MAX"))
    d_min = lo if lo is not None else (both if both is not None else DEFAULT_IMAGE_ALTERNATES_MIN)
    d_max = hi if hi is not None else (both if both is not None else DEFAULT_IMAGE_ALTERNATES_MAX)
    if d_max < d_min:
        d_max = d_min
    return d_min, d_max
