"""
I/O utilities for loading/saving timeline.json and canonical_text.txt.
Shared by preprocessing and ML scoring modules.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


def compute_text_hash(text: str) -> str:
    """Compute SHA256 hash of text content."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_timeline(book_dir: Path) -> Dict[str, Any]:
    """
    Load timeline.json from a book directory.
    
    Args:
        book_dir: Path to the book's data directory (e.g., data/book_id/)
    
    Returns:
        Parsed timeline dictionary
        
    Raises:
        FileNotFoundError: If timeline.json doesn't exist
        json.JSONDecodeError: If timeline.json is invalid
    """
    timeline_path = book_dir / "timeline.json"
    with open(timeline_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_timeline(book_dir: Path, timeline: Dict[str, Any]) -> Path:
    """
    Save timeline.json to a book directory.
    
    Args:
        book_dir: Path to the book's data directory
        timeline: Timeline dictionary to save
        
    Returns:
        Path to the saved timeline.json
    """
    timeline_path = book_dir / "timeline.json"
    with open(timeline_path, 'w', encoding='utf-8') as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
    return timeline_path


def load_canonical_text(book_dir: Path) -> Optional[str]:
    """
    Load canonical_text.txt from a book directory.
    
    Args:
        book_dir: Path to the book's data directory
        
    Returns:
        Canonical text string, or None if file doesn't exist
    """
    text_path = book_dir / "canonical_text.txt"
    if not text_path.exists():
        return None
    with open(text_path, 'r', encoding='utf-8') as f:
        return f.read()


def save_canonical_text(book_dir: Path, text: str) -> Tuple[Path, str]:
    """
    Save canonical_text.txt to a book directory.
    
    Args:
        book_dir: Path to the book's data directory
        text: Canonical text to save
        
    Returns:
        Tuple of (path to saved file, SHA256 hash of text)
    """
    text_path = book_dir / "canonical_text.txt"
    text_hash = compute_text_hash(text)
    
    with open(text_path, 'w', encoding='utf-8') as f:
        f.write(text)
    
    return text_path, text_hash


def load_ml_summary(book_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Load ml_summary.json from a book directory.
    
    Args:
        book_dir: Path to the book's data directory
        
    Returns:
        Parsed ML summary dictionary, or None if file doesn't exist
    """
    summary_path = book_dir / "ml_summary.json"
    if not summary_path.exists():
        return None
    with open(summary_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_ml_summary(book_dir: Path, summary: Dict[str, Any]) -> Path:
    """
    Save ml_summary.json to a book directory.
    
    Args:
        book_dir: Path to the book's data directory
        summary: ML summary dictionary to save
        
    Returns:
        Path to the saved ml_summary.json
    """
    summary_path = book_dir / "ml_summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary_path


def get_book_dir(data_dir: str, book_id: str) -> Path:
    """
    Get the book directory path, creating it if necessary.
    
    Args:
        data_dir: Base data directory (e.g., "data")
        book_id: Book identifier
        
    Returns:
        Path to the book's data directory
    """
    book_dir = Path(data_dir) / book_id
    book_dir.mkdir(parents=True, exist_ok=True)
    return book_dir


def check_canonical_text_valid(book_dir: Path, expected_hash: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Check if canonical_text.txt exists and optionally verify its hash.
    
    Args:
        book_dir: Path to the book's data directory
        expected_hash: Optional expected SHA256 hash to verify against
        
    Returns:
        Tuple of (is_valid, actual_hash)
        - is_valid is True if file exists and hash matches (if expected_hash provided)
        - actual_hash is the computed hash of the file, or None if file doesn't exist
    """
    text = load_canonical_text(book_dir)
    if text is None:
        return False, None
    
    actual_hash = compute_text_hash(text)
    
    if expected_hash is None:
        return True, actual_hash
    
    return actual_hash == expected_hash, actual_hash
