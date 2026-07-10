from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from src.core.text_utils import jaccard_similarity


class EmbeddingIndex:
    """Semantic index backed by sentence-transformers.

    Build from a list of database chunks (dict-like rows), then score
    queries against all stored vectors using cosine similarity.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self.model = None
        self.chunk_ids: list[int] = []
        self.embeddings: np.ndarray | None = None

    def available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            return True
        except Exception:
            return False

    def _load_model(self):
        if self.model is None and self.available():
            from sentence_transformers import SentenceTransformer  # type: ignore
            self.model = SentenceTransformer(self.model_name)
        return self.model

    def build(self, chunks: list[Any]) -> None:
        if not chunks:
            self.chunk_ids = []
            self.embeddings = None
            return
        model = self._load_model()
        if model is None:
            self.chunk_ids = [int(c["id"] if hasattr(c, "keys") else c.id) for c in chunks]
            self.embeddings = None
            return
        texts = [str(chunk["text_raw"] if hasattr(chunk, "keys") else chunk.text) for chunk in chunks]
        ids = [int(chunk["id"] if hasattr(chunk, "keys") else chunk.id) for chunk in chunks]
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        self.chunk_ids = ids
        self.embeddings = np.asarray(vectors, dtype=np.float32)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.embeddings is None:
            return
        np.save(path.with_suffix(".npy"), self.embeddings)
        path.with_suffix(".json").write_text(json.dumps({"chunk_ids": self.chunk_ids}, ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path) -> bool:
        npy = path.with_suffix(".npy")
        meta = path.with_suffix(".json")
        if not npy.exists() or not meta.exists():
            return False
        self.embeddings = np.load(npy)
        self.chunk_ids = json.loads(meta.read_text(encoding="utf-8"))["chunk_ids"]
        return True

    def score_query(self, query: str) -> dict[int, float]:
        if self.embeddings is None or not self.chunk_ids:
            return {}
        model = self._load_model()
        if model is None:
            return {}
        q = np.asarray(model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0], dtype=np.float32)
        scores = self.embeddings @ q
        return {chunk_id: float(score) for chunk_id, score in zip(self.chunk_ids, scores)}


def fallback_score(query: str, text: str) -> float:
    return jaccard_similarity(query, text)
