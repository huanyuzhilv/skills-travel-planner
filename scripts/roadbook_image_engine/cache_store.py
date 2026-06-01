"""SQLite 缓存：按 URL 存 phash 与尺寸，减少重复下载。"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_cache_root(repo_root: Path | None = None) -> Path:
    raw = os.environ.get("ROADBOOK_IMAGE_CACHE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "cache").resolve()


def db_path(cache_root: Path | None = None, repo_root: Path | None = None) -> Path:
    root = cache_root or default_cache_root(repo_root)
    meta = root / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    return meta / "image_meta.db"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_url TEXT NOT NULL UNIQUE,
            local_path TEXT,
            feed_id TEXT,
            keyword TEXT,
            phash TEXT,
            width INTEGER,
            height INTEGER,
            quality_score REAL,
            scene_type TEXT,
            shot_type TEXT,
            color_type TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_image_meta_phash ON image_meta(phash)")
    conn.commit()


class ImageMetaCache:
    def __init__(self, *, repo_root: Path | None = None, cache_root: Path | None = None) -> None:
        self._db = db_path(cache_root, repo_root)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(self._db)
        try:
            init_schema(conn)
        finally:
            conn.close()

    def get_by_url(self, image_url: str) -> dict[str, Any] | None:
        conn = _connect(self._db)
        try:
            row = conn.execute(
                "SELECT * FROM image_meta WHERE image_url = ? LIMIT 1",
                (image_url,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def upsert(
        self,
        image_url: str,
        *,
        phash: str | None = None,
        width: int | None = None,
        height: int | None = None,
        feed_id: str | None = None,
        keyword: str | None = None,
        local_path: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = _connect(self._db)
        try:
            conn.execute(
                """
                INSERT INTO image_meta (
                    image_url, local_path, feed_id, keyword, phash, width, height, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_url) DO UPDATE SET
                    local_path=excluded.local_path,
                    feed_id=COALESCE(excluded.feed_id, feed_id),
                    keyword=COALESCE(excluded.keyword, keyword),
                    phash=COALESCE(excluded.phash, phash),
                    width=COALESCE(excluded.width, width),
                    height=COALESCE(excluded.height, height)
                """,
                (
                    image_url,
                    local_path,
                    feed_id,
                    keyword,
                    phash,
                    width,
                    height,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
