"""Simple vector store and RAG memory using SQLite."""

import sqlite3
import json
import logging
from contextlib import closing
from pathlib import Path
from dataclasses import dataclass

from .embeddings import cosine_similarity

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_emails (
    message_id TEXT PRIMARY KEY,
    subject TEXT,
    sender TEXT,
    category TEXT,
    is_human_corrected INTEGER DEFAULT 0,
    embedding_json TEXT,
    stored_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

@dataclass
class RagContext:
    message_id: str
    subject: str
    sender: str
    category: str
    is_human_corrected: bool
    similarity: float

class RagStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with closing(sqlite3.connect(self._path)) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def save_email_context(self, message_id: str, subject: str, sender: str, category: str, embedding: list[float], is_human_corrected: bool = False):
        """Save an email's embedding and context to the RAG store."""
        embedding_json = json.dumps(embedding)
        with closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rag_emails (message_id, subject, sender, category, is_human_corrected, embedding_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, subject, sender, category, int(is_human_corrected), embedding_json)
            )
            conn.commit()

    def update_human_feedback(self, message_id: str, correct_category: str):
        """Update an email's category based on human feedback."""
        with closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                "UPDATE rag_emails SET category = ?, is_human_corrected = 1 WHERE message_id = ?",
                (correct_category, message_id)
            )
            conn.commit()

    def find_similar_emails(self, current_embedding: list[float], top_k: int = 3) -> list[RagContext]:
        """Find the most similar past emails using cosine similarity."""
        if not current_embedding or sum(current_embedding) == 0.0:
            return []
            
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute("SELECT message_id, subject, sender, category, is_human_corrected, embedding_json FROM rag_emails").fetchall()
            
        results = []
        for message_id, subject, sender, category, is_human_corrected, embedding_json in rows:
            try:
                past_embedding = json.loads(embedding_json)
                sim = cosine_similarity(current_embedding, past_embedding)
                results.append(RagContext(
                    message_id=message_id,
                    subject=subject,
                    sender=sender,
                    category=category,
                    is_human_corrected=bool(is_human_corrected),
                    similarity=sim
                ))
            except Exception:
                log.warning(f"Failed to parse embedding for {message_id}")
                continue
                
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results[:top_k]
