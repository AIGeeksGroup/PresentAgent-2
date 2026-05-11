"""
Document Processor - Sentence-level embedding and retrieval for video position localization.

Uses all-MiniLM-L6-v2 to generate embeddings for each sentence in a document.
When user asks a question, finds the most relevant sentence and calculates
the video position ratio based on word count.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np

# Lazy-load sentence-transformers to avoid blocking import
_sentence_model = None


def _get_sentence_model():
    """Get or initialize the sentence transformer model."""
    global _sentence_model
    if _sentence_model is None:
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # Force offline mode
        from sentence_transformers import SentenceTransformer
        
        # Find the snapshot path from huggingface cache
        cache_base = os.path.expanduser("~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots")
        snapshot_path = None
        if os.path.exists(cache_base):
            snapshots = os.listdir(cache_base)
            if snapshots:
                snapshot_path = os.path.join(cache_base, snapshots[0])
        
        if snapshot_path and os.path.exists(snapshot_path):
            print(f"[INFO] Loading sentence model from cache: {snapshot_path}")
            _sentence_model = SentenceTransformer(snapshot_path, device="cpu")
        else:
            print("[INFO] Loading sentence model from HuggingFace (offline fallback)")
            _sentence_model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    return _sentence_model


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class SentenceRecord:
    """Represents a single sentence with its position and embedding."""
    id: int
    text: str
    words_before: int  # Cumulative word count before this sentence
    embedding: List[float]


@dataclass
class SentenceIndex:
    """Index containing all sentences with their embeddings."""
    doc_path: str
    doc_hash: str  # MD5 hash of document content
    total_words: int
    sentences: List[SentenceRecord]
    model_name: str = "all-MiniLM-L6-v2"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "doc_path": self.doc_path,
            "doc_hash": self.doc_hash,
            "total_words": self.total_words,
            "model_name": self.model_name,
            "sentences": [
                {
                    "id": s.id,
                    "text": s.text,
                    "words_before": s.words_before,
                    "embedding": s.embedding,
                }
                for s in self.sentences
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SentenceIndex":
        """Create from dictionary."""
        sentences = [
            SentenceRecord(
                id=s["id"],
                text=s["text"],
                words_before=s["words_before"],
                embedding=s["embedding"],
            )
            for s in data["sentences"]
        ]
        return cls(
            doc_path=data["doc_path"],
            doc_hash=data["doc_hash"],
            total_words=data["total_words"],
            sentences=sentences,
            model_name=data.get("model_name", "all-MiniLM-L6-v2"),
        )


# ---------------------------------------------------------------------------
# Sentence Splitting
# ---------------------------------------------------------------------------

def split_into_sentences(text: str) -> List[str]:
    """
    Split text into sentences using regex.
    
    Handles both Chinese and English punctuation:
    - Chinese: 。！？...
    - English: . ! ?
    
    Returns list of non-empty sentence strings.
    """
    # Combined pattern for Chinese and English sentence endings
    pattern = r'[。！？.!?]+'
    raw_sentences = re.split(pattern, text)
    
    # Filter out empty strings and strip whitespace
    sentences = []
    for s in raw_sentences:
        s = s.strip()
        if s and len(s) > 1:  # Skip single characters
            sentences.append(s)
    
    return sentences


def count_words(text: str) -> int:
    """Count words in text using whitespace splitting."""
    return len(text.split())


def calculate_word_positions(sentences: List[str]) -> Tuple[List[int], int]:
    """
    Calculate cumulative word count before each sentence.
    
    Returns:
        - positions: List of word counts before each sentence
        - total_words: Total word count in all sentences
    """
    positions = []
    total = 0
    for s in sentences:
        positions.append(total)
        total += count_words(s)
    return positions, total


# ---------------------------------------------------------------------------
# Embedding Generation
# ---------------------------------------------------------------------------

def generate_embeddings(sentences: List[str]) -> List[List[float]]:
    """
    Generate embeddings for all sentences using all-MiniLM-L6-v2.
    
    Returns list of embedding vectors (384 dimensions each).
    """
    if not sentences:
        return []
    
    model = _get_sentence_model()
    embeddings = model.encode(sentences, convert_to_numpy=True)
    return embeddings.tolist()


def compute_hash(text: str) -> str:
    """Compute MD5 hash of text content."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Index Building
# ---------------------------------------------------------------------------

class DocumentProcessor:
    """Main class for document processing and embedding."""
    
    def __init__(self):
        self._index: Optional[SentenceIndex] = None
    
    def build_index(self, text: str, doc_path: str) -> SentenceIndex:
        """
        Build sentence index from document text.
        
        Args:
            text: Document content
            doc_path: Path to the document
            
        Returns:
            SentenceIndex with all sentences and embeddings
        """
        # Split into sentences
        sentences = split_into_sentences(text)
        if not sentences:
            return SentenceIndex(
                doc_path=doc_path,
                doc_hash=compute_hash(text),
                total_words=0,
                sentences=[],
            )
        
        # Calculate word positions
        positions, total_words = calculate_word_positions(sentences)
        
        # Generate embeddings
        embeddings = generate_embeddings(sentences)
        
        # Build sentence records
        records = []
        for i, (sent_text, pos, emb) in enumerate(zip(sentences, positions, embeddings)):
            records.append(
                SentenceRecord(
                    id=i,
                    text=sent_text,
                    words_before=pos,
                    embedding=emb,
                )
            )
        
        index = SentenceIndex(
            doc_path=doc_path,
            doc_hash=compute_hash(text),
            total_words=total_words,
            sentences=records,
        )
        
        self._index = index
        return index
    
    def load_index(self, file_path: str) -> Optional[SentenceIndex]:
        """
        Load sentence index from a JSON file.
        
        Args:
            file_path: Path to the index file
            
        Returns:
            SentenceIndex or None if file doesn't exist
        """
        path = Path(file_path)
        if not path.exists():
            return None
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            index = SentenceIndex.from_dict(data)
            self._index = index
            return index
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[WARN] Failed to load index from {file_path}: {e}")
            return None
    
    def save_index(self, file_path: str) -> bool:
        """
        Save sentence index to a JSON file.
        
        Args:
            file_path: Path to save the index
            
        Returns:
            True if successful, False otherwise
        """
        if self._index is None:
            return False
        
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._index.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except IOError as e:
            print(f"[WARN] Failed to save index to {file_path}: {e}")
            return False
    
    @property
    def current_index(self) -> Optional[SentenceIndex]:
        """Get the current index."""
        return self._index


# ---------------------------------------------------------------------------
# Similarity Search
# ---------------------------------------------------------------------------

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    
    Returns value in range [-1, 1], where 1 means identical direction.
    """
    a = np.array(a)
    b = np.array(b)
    
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(np.dot(a, b) / (norm_a * norm_b))


def find_relevant_position(
    question: str,
    index: SentenceIndex,
    top_k: int = 3,
) -> List[Tuple[int, float, float]]:
    """
    Find the most relevant sentences to the question.
    
    Args:
        question: User's question
        index: Sentence index to search
        top_k: Number of top results to return
        
    Returns:
        List of (sentence_id, similarity_score, position_ratio) tuples,
        sorted by similarity (highest first)
    """
    if not index.sentences:
        return []
    
    # Generate question embedding
    model = _get_sentence_model()
    question_emb = model.encode([question], convert_to_numpy=True)[0]
    
    # Compute similarity with all sentences
    results = []
    for sent in index.sentences:
        emb = np.array(sent.embedding)
        sim = float(np.dot(question_emb, emb) / (np.linalg.norm(question_emb) * np.linalg.norm(emb)))
        
        position_ratio = (
            max(0.10, min(0.90, sent.words_before / index.total_words))
            if index.total_words > 0
            else 0.10
        )
        
        results.append((sent.id, sim, position_ratio))
    
    # Sort by similarity and return top_k
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def find_best_position(question: str, index: SentenceIndex) -> Tuple[Optional[int], Optional[float]]:
    """
    Find the best position ratio for the question.
    
    Args:
        question: User's question
        index: Sentence index to search
        
    Returns:
        Tuple of (best_sentence_id, position_ratio) or (None, None) if no match
    """
    results = find_relevant_position(question, index, top_k=1)
    
    if not results:
        return None, None
    
    sent_id, _, position_ratio = results[0]
    return sent_id, position_ratio


# ---------------------------------------------------------------------------
# Keyword Detection
# ---------------------------------------------------------------------------

SEEK_KEYWORDS = [
    "跳到", "跳至", "转到", "定位", "去到", "去",
    "看", "播放", "快进", "快退", "回到",
    "第几页", "在哪", "这部分", "这个内容",
    "start", "jump", "seek", "go to", "play",
]

NAVIGATION_PHRASES = [
    "讲讲", "介绍", "说说", "解释", "详细说",
    "什么是", "怎么样", "如何", "为什么",
]


def needs_video_seek(question: str, index: Optional[SentenceIndex] = None) -> bool:
    """
    Determine if a question likely requires video seeking.
    
    Checks for navigation keywords or question patterns that suggest
    the user wants to see a specific part of the video.
    
    For Q&A mode, most questions are assumed to benefit from video positioning
    since the user is asking about content covered in the presentation.
    """
    # If there's no sentence index, skip seeking
    if index is None:
        index = get_cached_index()
    if index is None or not index.sentences:
        return False
    
    # Check for explicit seek keywords
    question_lower = question.lower()
    for keyword in SEEK_KEYWORDS:
        if keyword in question_lower or keyword in question:
            return True
    
    # Check for navigation phrases (combined with content reference)
    nav_count = sum(1 for p in NAVIGATION_PHRASES if p in question)
    if nav_count >= 1:
        # If it contains numbers, it's likely seeking
        if re.search(r"第[一二三四五六七八九十\d]+", question):
            return True
        if re.search(r"\d+页|\d+分", question):
            return True
        # For navigation phrases without numbers, assume seeking is useful
        # since user is asking about presentation content
        return True
    
    # Default: assume most questions about presentation content benefit from positioning
    # Only skip very short or generic questions
    if len(question.strip()) < 5:
        return False
    
    return True


# ---------------------------------------------------------------------------
# Index Cache
# ---------------------------------------------------------------------------

_global_index: Optional[SentenceIndex] = None
_index_file_path = Path(__file__).parent.parent / ".cache" / "sentence_index.json"


def get_cached_index() -> Optional[SentenceIndex]:
    """Get the globally cached index."""
    return _global_index


def set_cached_index(index: SentenceIndex) -> None:
    """Set the global index cache."""
    global _global_index
    _global_index = index


def load_cached_index() -> Optional[SentenceIndex]:
    """Load index from cache file if it exists."""
    if _index_file_path.exists():
        try:
            with open(_index_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            index = SentenceIndex.from_dict(data)
            set_cached_index(index)
            return index
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_cached_index(index: SentenceIndex) -> bool:
    """Save index to cache file."""
    _index_file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_index_file_path, "w", encoding="utf-8") as f:
            json.dump(index.to_dict(), f, ensure_ascii=False, indent=2)
        set_cached_index(index)
        return True
    except IOError:
        return False


# ---------------------------------------------------------------------------
# LLM-based Document Position Finder
# ---------------------------------------------------------------------------

def find_position_by_llm(
    question: str,
    source_content: str,
    api_key: str,
    base_url: str,
    model: str = "qwen3.5-omni-flash",
    max_paragraphs: int = 20,
) -> float | None:
    """
    Use LLM to find the most relevant position in the document for the question.

    Args:
        question: User's question
        source_content: Full document content
        api_key: API key for the LLM service
        base_url: Base URL for the API
        model: Model name
        max_paragraphs: Maximum number of paragraphs to sample (for speed)

    Returns:
        Position ratio (0.0-1.0) representing where in the document the answer
        is most likely found, or None if unable to determine.
    """
    import re
    from openai import OpenAI

    # Split document into segments (by paragraphs or sections)
    # Use double newlines as paragraph separators
    paragraphs = re.split(r'\n\s*\n', source_content)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return None

    # Sample paragraphs strategically instead of sending all
    # Strategy: take first 5, last 2, and evenly distribute the rest
    total_para = len(paragraphs)
    if total_para <= max_paragraphs:
        sampled_indices = list(range(total_para))
    else:
        # Take first 5, last 2, and spread the rest evenly
        sampled_indices = list(range(5))
        remaining_slots = max_paragraphs - 7  # 5 first + 2 last
        if remaining_slots > 0:
            step = (total_para - 7) / remaining_slots
            for i in range(remaining_slots):
                idx = 5 + int(i * step)
                if idx not in sampled_indices and idx < total_para - 2:
                    sampled_indices.append(idx)
        sampled_indices.extend(range(total_para - 2, total_para))
        sampled_indices = sorted(set(sampled_indices))

    sampled_paragraphs = [paragraphs[i] for i in sampled_indices]

    # Create a prompt that asks the LLM to identify which paragraph is most relevant
    # and estimate its position in the document
    paragraph_summaries = []
    for i, para in enumerate(sampled_paragraphs):
        # Truncate long paragraphs for the prompt
        preview = para[:150] + "..." if len(para) > 150 else para
        original_idx = sampled_indices[i] + 1  # 1-based
        paragraph_summaries.append(f"[Para {original_idx}] {preview}")

    prompt = f"""You are helping find the relevant part of a document for a user's question.

User question: {question}

Here are sampled paragraphs from the document:
{chr(10).join(paragraph_summaries)}

Based on the user question, identify which paragraph contains the most relevant information.
You must respond with ONLY a JSON object in this exact format (no other text):
{{"paragraph_index": <number>, "reason": "<brief reason>"}}

Where paragraph_index is the original paragraph number (e.g., 1, 15, 30).
Choose the paragraph that best answers the question.
"""

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=100,
        )

        content = response.choices[0].message.content.strip()

        # Parse the JSON response
        # Handle potential markdown code blocks
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            para_index = result.get("paragraph_index", 1)
            # Convert to 0-based index and clamp
            idx = max(0, min(para_index - 1, total_para - 1))
            # Calculate position ratio
            position_ratio = (idx + 0.5) / total_para
            print(f"[INFO] LLM located question at paragraph {para_index}/{total_para}, position: {position_ratio:.3f}")
            return position_ratio

    except Exception as e:
        print(f"[WARN] LLM position lookup failed: {e}")

    return None
