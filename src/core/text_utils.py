from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

PUNCT_RE = re.compile(r"[\s　,，。.!！?？;；:：、\-—_~·`'\"“”‘’（）()\[\]【】{}<>《》|/\\]+")
TOKEN_KEEP_RE = re.compile(r"[一-鿿A-Za-z0-9]+")
TRADITIONAL_MAP = str.maketrans(
    {
        "東": "东", "關": "关", "開": "开", "來": "来", "這": "这", "個": "个",
        "裡": "里", "裏": "里", "麼": "么", "為": "为", "還": "还", "對": "对",
        "說": "说", "沒": "没", "會": "会", "讓": "让", "兒": "儿", "親": "亲",
        "給": "给", "進": "进", "過": "过", "時": "时", "後": "后", "們": "们",
        "頭": "头", "風": "风", "雪": "雪", "餓": "饿", "貧": "贫", "窮": "穷",
        "戰": "战", "亂": "乱", "歷": "历", "話": "话", "線": "线", "長": "长",
    }
)

DOMAIN_TERMS = [
    "朱开山", "文他娘", "鲜儿", "传文", "传武", "传杰", "朱家", "闯关东",
    "山海关", "山东", "东北", "离乡", "逃荒", "饥荒", "活路", "老屋", "饭桌",
]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(TRADITIONAL_MAP)
    text = text.replace("﻿", " ")
    text = re.sub(r"[`*_>#|]", " ", text)
    text = PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


@lru_cache(maxsize=1)
def _jieba_module():
    try:
        import jieba  # type: ignore
        for term in DOMAIN_TERMS:
            jieba.add_word(term, freq=200000)
        return jieba
    except Exception:
        return None


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    jieba = _jieba_module()
    if jieba is not None:
        raw_tokens = list(jieba.cut(normalized, cut_all=False))
    else:
        raw_tokens = TOKEN_KEEP_RE.findall(normalized)
    tokens: list[str] = []
    for token in raw_tokens:
        token = token.strip().lower()
        if not token:
            continue
        if TOKEN_KEEP_RE.fullmatch(token):
            tokens.append(token)
    return tokens


def tokenized_text(text: str) -> str:
    return " ".join(tokenize(text))


def extract_entities(text: str, entity_terms: list[str] | None = None) -> list[str]:
    terms = entity_terms or DOMAIN_TERMS
    return [term for term in terms if term in text]


def jaccard_similarity(a: str, b: str) -> float:
    ta = set(tokenize(a))
    tb = set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def summarize(text: str, max_len: int = 80) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
