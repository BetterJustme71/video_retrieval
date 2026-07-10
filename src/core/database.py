from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from src.core.models import QuerySegment, SearchChunk, SearchMatch, TranscriptSegment, VideoInfo
from src.core.text_utils import tokenized_text

SCHEMA_VERSION = 1


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                normalized_filename TEXT NOT NULL,
                episode_no INTEGER,
                duration_ms INTEGER,
                size_bytes INTEGER NOT NULL,
                mtime REAL NOT NULL,
                fingerprint TEXT NOT NULL,
                has_audio INTEGER NOT NULL,
                has_subtitle INTEGER NOT NULL,
                video_streams_json TEXT NOT NULL,
                audio_streams_json TEXT NOT NULL,
                subtitle_streams_json TEXT NOT NULL,
                asr_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transcript_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                text_raw TEXT NOT NULL,
                text_norm TEXT NOT NULL,
                tokenized TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS search_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                text_raw TEXT NOT NULL,
                text_norm TEXT NOT NULL,
                tokenized TEXT NOT NULL,
                segment_ids_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS query_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                params_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS query_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES query_runs(id) ON DELETE CASCADE,
                segment_index INTEGER NOT NULL,
                section_name TEXT NOT NULL,
                query_type TEXT NOT NULL,
                query_text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES query_runs(id) ON DELETE CASCADE,
                query_segment_id INTEGER,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                preview_start_ms INTEGER NOT NULL,
                preview_end_ms INTEGER NOT NULL,
                evidence_text TEXT NOT NULL,
                text_score REAL NOT NULL,
                semantic_score REAL NOT NULL,
                entity_score REAL NOT NULL,
                episode_score REAL NOT NULL,
                final_score REAL NOT NULL,
                user_status TEXT NOT NULL DEFAULT '待确认'
            );
            """
        )
        self._init_fts()
        self.conn.execute(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def _init_fts(self) -> None:
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_chunks_fts USING fts5(tokenized, content='search_chunks', content_rowid='id')"
        )

    def upsert_video(self, info: VideoInfo) -> int:
        existing = self.conn.execute("SELECT id FROM videos WHERE path = ?", (str(info.path),)).fetchone()
        payload = (
            str(info.path),
            info.filename,
            info.normalized_filename,
            info.episode_no,
            info.duration_ms,
            info.size_bytes,
            info.mtime,
            info.fingerprint,
            int(info.has_audio),
            int(info.has_subtitle),
            json.dumps(info.video_streams, ensure_ascii=False),
            json.dumps(info.audio_streams, ensure_ascii=False),
            json.dumps(info.subtitle_streams, ensure_ascii=False),
        )
        if existing:
            video_id = int(existing["id"])
            self.conn.execute(
                """
                UPDATE videos SET filename=?, normalized_filename=?, episode_no=?, duration_ms=?,
                    size_bytes=?, mtime=?, fingerprint=?, has_audio=?, has_subtitle=?,
                    video_streams_json=?, audio_streams_json=?, subtitle_streams_json=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                payload[1:] + (video_id,),
            )
        else:
            cur = self.conn.execute(
                """
                INSERT INTO videos(path, filename, normalized_filename, episode_no, duration_ms,
                    size_bytes, mtime, fingerprint, has_audio, has_subtitle,
                    video_streams_json, audio_streams_json, subtitle_streams_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            video_id = int(cur.lastrowid)
        self.conn.commit()
        return video_id

    def list_videos(self, episode: int | None = None) -> list[sqlite3.Row]:
        if episode is None:
            rows = self.conn.execute(
                "SELECT * FROM videos ORDER BY episode_no IS NULL, episode_no, filename"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM videos WHERE episode_no=? ORDER BY filename", (episode,)
            ).fetchall()
        return list(rows)

    def get_video(self, video_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()

    def transcript_exists(self, video_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM transcript_segments WHERE video_id=? LIMIT 1", (video_id,)
        ).fetchone()
        return row is not None

    def replace_transcript(self, video_id: int, segments: Iterable[TranscriptSegment]) -> None:
        self.conn.execute("DELETE FROM transcript_segments WHERE video_id=?", (video_id,))
        self.conn.execute("DELETE FROM search_chunks WHERE video_id=?", (video_id,))
        rows = []
        for seg in segments:
            norm = tokenized_text(seg.text)
            rows.append((video_id, seg.start_ms, seg.end_ms, seg.text, norm, norm))
        self.conn.executemany(
            """
            INSERT INTO transcript_segments(video_id, start_ms, end_ms, text_raw, text_norm, tokenized)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.execute("UPDATE videos SET asr_status='done', updated_at=CURRENT_TIMESTAMP WHERE id=?", (video_id,))
        self.conn.commit()

    def load_segments(self, video_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM transcript_segments WHERE video_id=? ORDER BY start_ms, id", (video_id,)
            ).fetchall()
        )

    def replace_chunks(self, video_id: int, chunks: Iterable[SearchChunk]) -> None:
        old_ids = [r["id"] for r in self.conn.execute("SELECT id FROM search_chunks WHERE video_id=?", (video_id,)).fetchall()]
        if old_ids:
            self.conn.executemany("DELETE FROM search_chunks_fts WHERE rowid=?", [(i,) for i in old_ids])
        self.conn.execute("DELETE FROM search_chunks WHERE video_id=?", (video_id,))
        for chunk in chunks:
            tokenized = tokenized_text(chunk.text)
            cur = self.conn.execute(
                """
                INSERT INTO search_chunks(video_id, start_ms, end_ms, text_raw, text_norm, tokenized, segment_ids_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    chunk.start_ms,
                    chunk.end_ms,
                    chunk.text,
                    tokenized,
                    tokenized,
                    json.dumps(chunk.segment_ids, ensure_ascii=False),
                ),
            )
            chunk_id = int(cur.lastrowid)
            self.conn.execute(
                "INSERT INTO search_chunks_fts(rowid, tokenized) VALUES (?, ?)",
                (chunk_id, tokenized),
            )
        self.conn.commit()

    def list_chunks(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT c.*, v.path AS video_path, v.filename AS video_filename, v.episode_no, v.duration_ms
                FROM search_chunks c JOIN videos v ON v.id = c.video_id
                ORDER BY v.episode_no IS NULL, v.episode_no, c.start_ms
                """
            ).fetchall()
        )

    def search_fts(self, query_tokenized: str, limit: int = 100) -> list[sqlite3.Row]:
        if not query_tokenized.strip():
            return []
        terms = [t for t in query_tokenized.split() if len(t) > 1 or t.isdigit()]
        if not terms:
            return []

        # Prefer phrase-like AND for precision, then fall back to OR for recall.
        attempts = [" ".join(terms[:12]), " OR ".join(terms[:24])]
        seen: set[int] = set()
        results: list[sqlite3.Row] = []
        for fts_query in attempts:
            if not fts_query.strip():
                continue
            try:
                rows = self.conn.execute(
                    """
                    SELECT c.*, v.path AS video_path, v.filename AS video_filename, v.episode_no, v.duration_ms,
                           bm25(search_chunks_fts) AS rank
                    FROM search_chunks_fts
                    JOIN search_chunks c ON c.id = search_chunks_fts.rowid
                    JOIN videos v ON v.id = c.video_id
                    WHERE search_chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for row in rows:
                row_id = int(row["id"])
                if row_id not in seen:
                    results.append(row)
                    seen.add(row_id)
            if len(results) >= limit:
                break
        return results[:limit]

    def create_query_run(self, script_path: Path, params: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO query_runs(script_path, params_json) VALUES (?, ?)",
            (str(script_path), json.dumps(params, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def save_query_segments(self, run_id: int, queries: list[QuerySegment]) -> dict[int, int]:
        mapping: dict[int, int] = {}
        for query in queries:
            cur = self.conn.execute(
                """
                INSERT INTO query_segments(run_id, segment_index, section_name, query_type, query_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, query.index, query.section, query.query_type, query.text),
            )
            mapping[query.index] = int(cur.lastrowid)
        self.conn.commit()
        return mapping

    def save_matches(self, run_id: int, matches: list[SearchMatch], query_id_by_index: dict[int, int]) -> None:
        rows = []
        for match in matches:
            rows.append(
                (
                    run_id,
                    query_id_by_index.get(match.query_index),
                    match.video_id,
                    match.start_ms,
                    match.end_ms,
                    match.preview_start_ms,
                    match.preview_end_ms,
                    match.evidence_text,
                    match.text_score,
                    match.semantic_score,
                    match.entity_score,
                    match.episode_score,
                    match.final_score,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO matches(run_id, query_segment_id, video_id, start_ms, end_ms,
                preview_start_ms, preview_end_ms, evidence_text, text_score, semantic_score,
                entity_score, episode_score, final_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
