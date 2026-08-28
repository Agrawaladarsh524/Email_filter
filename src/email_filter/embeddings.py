"""Semantic embedding generation and vector math."""

import logging
import numpy as np
from openai import OpenAI

from .config import get_settings
from .classifier import _client

log = logging.getLogger(__name__)

def get_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """Get the semantic embedding vector for a given text."""
    if not text or not text.strip():
        # Return a zero vector if there's no text, 1536 is the dimension of text-embedding-3-small
        return [0.0] * 1536
        
    try:
        response = _client.embeddings.create(
            input=text,
            model=model
        )
        return response.data[0].embedding
    except Exception:
        log.exception("Failed to fetch embedding from OpenAI")
        return [0.0] * 1536

def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate the cosine similarity between two vectors. Returns 0.0 to 1.0."""
    a = np.array(vec1)
    b = np.array(vec2)
    
    # Avoid division by zero
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
        
    return float(np.dot(a, b) / (norm_a * norm_b))
