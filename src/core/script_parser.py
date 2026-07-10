from __future__ import annotations

import re
from pathlib import Path

from src.core.models import QuerySegment
from src.core.text_utils import normalize_text

HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*+]|^\s*\d+[.)、]")
LOW_VALUE_KEYWORDS = ["封面", "爆点句", "标题", "下一期", "发布", "账号", "简介"]
HIGH_VALUE_KEYWORDS = ["完整口播", "口播文案", "剪辑画面", "画面建议", "剧情", "人物", "主题", "细节"]


def read_text_guess(path: Path) -> str:
    for encoding in ["utf-8", "utf-8-sig", "gb18030"]:
        try:
            return Path(path).read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    results: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) < 35:
            buf += part
            continue
        if buf:
            results.append(buf)
        buf = part
    if buf:
        results.append(buf)
    # Keep queries useful but not too long.
    final: list[str] = []
    for item in results:
        if len(item) <= 140:
            final.append(item)
        else:
            chunks = [item[i : i + 120] for i in range(0, len(item), 120)]
            final.extend(chunks)
    return final


def clean_script_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^>+\s*", "", line)
    line = BULLET_RE.sub("", line).strip()
    if line.count("|") >= 2:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        line = "。".join(cell for cell in cells if cell and not re.fullmatch(r"[-:：\s]+", cell))
    line = re.sub(r"^[\d:：\-—\s]+", "", line).strip()
    line = re.sub(r"\*\*|__|`", "", line)
    line = re.sub(r"【[^】]{1,20}】", "", line)
    return line.strip()


class ScriptParser:
    def __init__(self, path: Path):
        self.path = Path(path)

    def parse(self) -> list[QuerySegment]:
        if not self.path.exists():
            raise FileNotFoundError(f"文案文件不存在：{self.path}")
        text = read_text_guess(self.path)
        sections = self._sections(text)
        segments: list[QuerySegment] = []
        index = 1
        for section, body in sections:
            if self._is_low_value(section):
                continue
            query_type = self._query_type(section)
            weight_section = self._is_high_value(section)
            for line in self._candidate_lines(body):
                for sentence in split_sentences(line):
                    sentence_norm = normalize_text(sentence)
                    if len(sentence_norm) < 6:
                        continue
                    if not weight_section and len(sentence_norm) < 12:
                        continue
                    segments.append(QuerySegment(index=index, section=section, query_type=query_type, text=sentence.strip()))
                    index += 1
        return segments[:120]

    def _sections(self, text: str) -> list[tuple[str, str]]:
        current = "正文"
        buf: list[str] = []
        sections: list[tuple[str, str]] = []
        for line in text.splitlines():
            match = HEADING_RE.match(line)
            if match:
                if buf:
                    sections.append((current, "\n".join(buf)))
                current = match.group(2).strip()
                buf = []
            else:
                buf.append(line)
        if buf:
            sections.append((current, "\n".join(buf)))
        return sections

    def _candidate_lines(self, body: str) -> list[str]:
        lines: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = clean_script_line(line)
            if len(normalize_text(line)) >= 6:
                lines.append(line)
        return lines

    def _is_low_value(self, section: str) -> bool:
        return any(keyword in section for keyword in LOW_VALUE_KEYWORDS)

    def _is_high_value(self, section: str) -> bool:
        return any(keyword in section for keyword in HIGH_VALUE_KEYWORDS)

    def _query_type(self, section: str) -> str:
        if "画面" in section or "剪辑" in section:
            return "画面建议"
        if "人物" in section:
            return "人物主题"
        if "口播" in section or "文案" in section:
            return "口播"
        return "主题"
