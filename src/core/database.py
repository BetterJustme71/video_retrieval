from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from src.core.models import (
    MATCH_STATUSES,
    MATCH_STATUS_PENDING,
    QuerySegment,
    SearchChunk,
    SearchMatch,
    TranscriptSegment,
    VideoInfo,
)
from src.core.text_utils import tokenized_text

SCHEMA_VERSION = 4


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
                transcript_fingerprint TEXT,
                transcript_source TEXT,
                transcript_source_ref TEXT,
                transcript_source_fingerprint TEXT,
                chunks_fingerprint TEXT,
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
                user_status TEXT NOT NULL DEFAULT '待定'
            );
            """
        )
        self._migrate_schema()
        self._init_fts()
        self.conn.execute(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def _migrate_schema(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(videos)").fetchall()}
        if "transcript_fingerprint" not in columns:
            self.conn.execute("ALTER TABLE videos ADD COLUMN transcript_fingerprint TEXT")
        if "transcript_source" not in columns:
            self.conn.execute("ALTER TABLE videos ADD COLUMN transcript_source TEXT")
        if "transcript_source_ref" not in columns:
            self.conn.execute("ALTER TABLE videos ADD COLUMN transcript_source_ref TEXT")
        if "transcript_source_fingerprint" not in columns:
            self.conn.execute("ALTER TABLE videos ADD COLUMN transcript_source_fingerprint TEXT")
        if "chunks_fingerprint" not in columns:
            self.conn.execute("ALTER TABLE videos ADD COLUMN chunks_fingerprint TEXT")
        self.conn.execute("UPDATE matches SET user_status=? WHERE user_status=?", (MATCH_STATUS_PENDING, "待确认"))
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

    def count_chunks(self, video_id: int | None = None) -> int:
        if video_id is None:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM search_chunks").fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM search_chunks WHERE video_id=?", (video_id,)).fetchone()
        return int(row["count"] if row is not None else 0)

    def update_transcript_metadata(
        self,
        video_id: int,
        source: str | None,
        source_ref: str | None,
        source_fingerprint: str | None,
        transcript_fingerprint: str | None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE videos
            SET transcript_source=?, transcript_source_ref=?, transcript_source_fingerprint=?,
                transcript_fingerprint=?, chunks_fingerprint=NULL, asr_status=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (source, source_ref, source_fingerprint, transcript_fingerprint, source or "done", video_id),
        )
        self.conn.commit()

    def update_transcript_fingerprint(self, video_id: int, fingerprint: str | None) -> None:
        self.update_transcript_metadata(video_id, "asr", None, fingerprint, fingerprint)

    def update_chunks_fingerprint(self, video_id: int, fingerprint: str | None) -> None:
        self.conn.execute(
            "UPDATE videos SET chunks_fingerprint=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (fingerprint, video_id),
        )
        self.conn.commit()

    def chunk_fingerprint_rows(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT id, fingerprint, transcript_source, transcript_source_ref,
                       transcript_source_fingerprint, transcript_fingerprint, chunks_fingerprint
                FROM videos
                WHERE id IN (SELECT DISTINCT video_id FROM search_chunks)
                ORDER BY id
                """
            ).fetchall()
        )

    def replace_transcript(self, video_id: int, segments: Iterable[TranscriptSegment]) -> None:
        old_ids = [r["id"] for r in self.conn.execute("SELECT id FROM search_chunks WHERE video_id=?", (video_id,)).fetchall()]
        if old_ids:
            self.conn.executemany("DELETE FROM search_chunks_fts WHERE rowid=?", [(i,) for i in old_ids])
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
        self.conn.execute(
            "UPDATE videos SET asr_status='done', chunks_fingerprint=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (video_id,),
        )
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
                SELECT c.*, v.path AS video_path, v.filename AS video_filename, v.episode_no, v.duration_ms,
                       v.fingerprint AS video_fingerprint,
                       v.transcript_source AS transcript_source,
                       v.transcript_source_ref AS transcript_source_ref,
                       v.transcript_source_fingerprint AS transcript_source_fingerprint,
                       v.transcript_fingerprint AS transcript_fingerprint,
                       v.chunks_fingerprint AS chunks_fingerprint
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
                           v.fingerprint AS video_fingerprint,
                           v.transcript_fingerprint AS transcript_fingerprint,
                           v.chunks_fingerprint AS chunks_fingerprint,
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
        for match in matches:
            query_segment_id = query_id_by_index.get(match.query_index)
            cur = self.conn.execute(
                """
                INSERT INTO matches(run_id, query_segment_id, video_id, start_ms, end_ms,
                    preview_start_ms, preview_end_ms, evidence_text, text_score, semantic_score,
                    entity_score, episode_score, final_score, user_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    query_segment_id,
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
                    match.status,
                ),
            )
            match.match_id = int(cur.lastrowid)
            match.run_id = run_id
            match.query_segment_id = query_segment_id
        self.conn.commit()

    def update_match_statuses(self, match_ids: list[int], status: str) -> None:
        if status not in MATCH_STATUSES:
            raise ValueError(f"无效状态：{status}")
        ids = [int(match_id) for match_id in match_ids if match_id is not None]
        if not ids:
            return
        self.conn.executemany(
            "UPDATE matches SET user_status=? WHERE id=?",
            [(status, match_id) for match_id in ids],
        )
        self.conn.commit()

    def get_latest_query_run(self, script_path: Path | None = None) -> sqlite3.Row | None:
        if script_path is not None:
            row = self.conn.execute(
                "SELECT * FROM query_runs WHERE script_path=? ORDER BY id DESC LIMIT 1",
                (str(script_path),),
            ).fetchone()
            if row is not None:
                return row
        return self.conn.execute("SELECT * FROM query_runs ORDER BY id DESC LIMIT 1").fetchone()

    def load_matches_for_run(self, run_id: int) -> list[SearchMatch]:
        rows = self.conn.execute(
            """
            SELECT
                m.id AS match_id,
                m.run_id,
                m.query_segment_id,
                m.user_status,
                qs.segment_index,
                qs.section_name,
                qs.query_type,
                qs.query_text,
                v.id AS video_id,
                v.path AS video_path,
                v.filename AS video_filename,
                v.episode_no,
                m.start_ms,
                m.end_ms,
                m.preview_start_ms,
                m.preview_end_ms,
                m.evidence_text,
                m.text_score,
                m.semantic_score,
                m.entity_score,
                m.episode_score,
                m.final_score
            FROM matches m
            JOIN query_segments qs ON qs.id = m.query_segment_id
            JOIN videos v ON v.id = m.video_id
            WHERE m.run_id = ?
            ORDER BY qs.segment_index, m.final_score DESC, m.id
            """,
            (run_id,),
        ).fetchall()
        matches: list[SearchMatch] = []
        for row in rows:
            matches.append(
                SearchMatch(
                    query_index=int(row["segment_index"]),
                    query_text=str(row["query_text"]),
                    query_type=str(row["query_type"]),
                    section=str(row["section_name"]),
                    video_id=int(row["video_id"]),
                    video_path=str(row["video_path"]),
                    video_filename=str(row["video_filename"]),
                    episode_no=int(row["episode_no"]) if row["episode_no"] is not None else None,
                    start_ms=int(row["start_ms"]),
                    end_ms=int(row["end_ms"]),
                    preview_start_ms=int(row["preview_start_ms"]),
                    preview_end_ms=int(row["preview_end_ms"]),
                    evidence_text=str(row["evidence_text"]),
                    text_score=float(row["text_score"]),
                    semantic_score=float(row["semantic_score"]),
                    entity_score=float(row["entity_score"]),
                    episode_score=float(row["episode_score"]),
                    final_score=float(row["final_score"]),
                    status=str(row["user_status"] or MATCH_STATUS_PENDING),
                    match_id=int(row["match_id"]),
                    run_id=int(row["run_id"]),
                    query_segment_id=int(row["query_segment_id"]) if row["query_segment_id"] is not None else None,
                )
            )
        return matches
