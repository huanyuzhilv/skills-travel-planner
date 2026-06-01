"""从仓库根目录加载 ``.env`` 到 ``os.environ``（不依赖 python-dotenv）。

规则：
  - 仅解析 ``KEY=VALUE`` 行；忽略空行与 ``#`` 行首注释；
  - 支持可选前缀 ``export ``；
  - 值首尾单/双引号会剥除；
  - 默认 **不覆盖** 已存在且**非空**的环境变量；若 shell 里误 export 了空值，仍会用 ``.env`` 中的非空值覆盖。

供 ``deliver_roadbook_v2.py``、``enrich_daily_descriptions_from_xhs.py`` 等在读取
``OPENAI_API_KEY`` / ``DEEPSEEK_API_KEY`` 之前调用。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_KEY_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_repo_dotenv(repo_root: Path, *, override: bool = False) -> int:
    """读取 ``repo_root / ".env"`` 并写入 ``os.environ``。返回成功解析的条目数（0 表示无文件或无有效行）。"""
    path = repo_root / ".env"
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    n = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or not _KEY_OK.match(key):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if override:
            os.environ[key] = val
            n += 1
        elif key not in os.environ or not str(os.environ.get(key, "")).strip():
            os.environ[key] = val
            n += 1
    return n
