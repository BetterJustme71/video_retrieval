from __future__ import annotations

import argparse
from pathlib import Path

from src.app.config import CONFIG, AppConfig
from src.core.clipper import export_clip
from src.core.edit_list import export_editing_checklist
from src.core.database import Database
from src.core.exporter import export_matches_csv, export_matches_json
from src.core.index_manager import rebuild_chunks_for_video
from src.core.media_scanner import MediaScanner
from src.core.retrieval_engine import RetrievalEngine
from src.core.script_parser import ScriptParser
from src.core.timecode import ms_to_timecode
from src.core.thumbnailer import export_thumbnail
from src.core.transcriber import ensure_transcript


def init_db(config: AppConfig = CONFIG) -> Database:
    config.ensure_dirs()
    db = Database(config.db_path)
    db.init_schema()
    return db


def scan_videos(video_dir: Path, config: AppConfig = CONFIG) -> list:
    db = init_db(config)
    try:
        infos = MediaScanner(video_dir).scan(probe=True)
        for info in infos:
            db.upsert_video(info)
        return db.list_videos()
    finally:
        db.close()


def index_videos(video_dir: Path, episodes: list[int] | None, model: str, config: AppConfig = CONFIG, progress=print) -> None:
    db = init_db(config)
    try:
        infos = MediaScanner(video_dir).scan(probe=True)
        video_ids: list[int] = []
        for info in infos:
            video_id = db.upsert_video(info)
            if episodes is None or info.episode_no in episodes:
                video_ids.append(video_id)
        for video_id in video_ids:
            row = db.get_video(video_id)
            if row is None:
                continue
            path = Path(row["path"])
            transcript_result = ensure_transcript(
                db,
                video_id,
                path,
                model_size=model,
                device=config.whisper_device,
                compute_type=config.whisper_compute_type,
                progress=progress,
            )
            if transcript_result.reused:
                progress(f"字幕/转写已复用：{path.name} / {transcript_result.source} / {transcript_result.source_ref} / {transcript_result.segment_count} 段")
            else:
                progress(f"字幕/转写已更新：{path.name} / {transcript_result.source} / {transcript_result.reason} / {transcript_result.segment_count} 段")
            chunk_result = rebuild_chunks_for_video(
                db,
                video_id,
                min_ms=config.chunk_min_ms,
                max_ms=config.chunk_max_ms,
                overlap_segments=config.chunk_overlap_segments,
            )
            if chunk_result.rebuilt:
                progress(f"已重建检索块：{path.name} / {chunk_result.chunk_count} 个 / {chunk_result.reason}")
            else:
                progress(f"复用已有检索块：{path.name} / {chunk_result.chunk_count} 个")
    finally:
        db.close()


def search_script(script_path: Path, top_k: int, config: AppConfig = CONFIG) -> list:
    db = init_db(config)
    try:
        queries = ScriptParser(script_path).parse()
        run_id = db.create_query_run(script_path, {"top_k": top_k, "query_count": len(queries)})
        query_id_by_index = db.save_query_segments(run_id, queries)
        engine = RetrievalEngine(db, config)
        matches = engine.search_queries(queries, top_k=top_k)
        db.save_matches(run_id, matches, query_id_by_index)
        stamp = script_path.stem[:32].replace(" ", "_")
        csv_path = config.exports_dir / f"{stamp}_matches.csv"
        json_path = config.exports_dir / f"{stamp}_matches.json"
        export_matches_csv(matches, csv_path)
        export_matches_json(matches, json_path)
        return matches, csv_path, json_path
    finally:
        db.close()


def run_gui() -> int:
    from src.ui.main_window import run_app

    return run_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="视频片段检索工具")
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="扫描视频目录")
    scan.add_argument("--video-dir", type=Path, default=CONFIG.default_video_dir)

    index = sub.add_parser("index", help="转写并索引视频")
    index.add_argument("--video-dir", type=Path, default=CONFIG.default_video_dir)
    index.add_argument("--episodes", type=str, default="1", help="逗号分隔集数；all 表示全量")
    index.add_argument("--model", type=str, default=CONFIG.whisper_model)

    search = sub.add_parser("search", help="搜索脚本文案")
    search.add_argument("--script", type=Path, default=CONFIG.default_script_path)
    search.add_argument("--top-k", type=int, default=5)

    clip = sub.add_parser("clip", help="搜索并导出前几条候选片段")
    clip.add_argument("--script", type=Path, default=CONFIG.default_script_path)
    clip.add_argument("--top-k", type=int, default=1)
    clip.add_argument("--limit", type=int, default=1)
    clip.add_argument("--output-dir", type=Path, default=CONFIG.exports_dir / "clips")

    checklist = sub.add_parser("checklist", help="搜索并导出剪辑清单（默认取前若干候选作为可用）")
    checklist.add_argument("--script", type=Path, default=CONFIG.default_script_path)
    checklist.add_argument("--top-k", type=int, default=1)
    checklist.add_argument("--limit", type=int, default=20)
    checklist.add_argument("--output-dir", type=Path, default=CONFIG.exports_dir / "edit_lists")

    thumbnail = sub.add_parser("thumbnail", help="搜索并导出前几条候选片段缩略图")
    thumbnail.add_argument("--script", type=Path, default=CONFIG.default_script_path)
    thumbnail.add_argument("--top-k", type=int, default=1)
    thumbnail.add_argument("--limit", type=int, default=5)
    thumbnail.add_argument("--output-dir", type=Path, default=CONFIG.exports_dir / "thumbnails")

    sub.add_parser("gui", help="启动桌面界面")
    return parser


def parse_episodes(value: str) -> list[int] | None:
    if value.strip().lower() in {"all", "*", "全部"}:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "gui"
    if command == "scan":
        rows = scan_videos(args.video_dir)
        print(f"扫描到 {len(rows)} 个视频：")
        for row in rows:
            print(f"第{row['episode_no'] or '?'}集\t{ms_to_timecode(row['duration_ms'])}\t{row['filename']}\t字幕:{'是' if row['has_subtitle'] else '否'}")
        return 0
    if command == "index":
        index_videos(args.video_dir, parse_episodes(args.episodes), args.model)
        return 0
    if command == "search":
        matches, csv_path, json_path = search_script(args.script, args.top_k)
        print(f"得到 {len(matches)} 条候选结果")
        print(f"CSV: {csv_path}")
        print(f"JSON: {json_path}")
        for match in matches[:20]:
            print(f"[{match.final_score:.3f}] 第{match.episode_no or '?'}集 {ms_to_timecode(match.start_ms)}-{ms_to_timecode(match.end_ms)} {match.video_filename} :: {match.query_text[:40]}")
        return 0
    if command == "clip":
        matches, _csv_path, _json_path = search_script(args.script, args.top_k)
        for match in matches[: args.limit]:
            path = export_clip(match, args.output_dir, use_preview_range=True, progress=print)
            print(path)
        return 0
    if command == "checklist":
        matches, _csv_path, _json_path = search_script(args.script, args.top_k)
        selected = matches[: args.limit]
        for match in selected:
            match.status = "可用"
        csv_path, json_path = export_editing_checklist(selected, args.output_dir)
        print(f"剪辑清单 CSV: {csv_path}")
        print(f"剪辑清单 JSON: {json_path}")
        return 0
    if command == "thumbnail":
        matches, _csv_path, _json_path = search_script(args.script, args.top_k)
        for match in matches[: args.limit]:
            path = export_thumbnail(match, args.output_dir, progress=print)
            print(path)
        return 0
    if command == "gui":
        return run_gui()
    parser.print_help()
    return 1
