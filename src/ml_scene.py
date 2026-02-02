"""
Zero-shot NLI scene classification for EPUB chunks.

Uses HuggingFace Transformers zero-shot-classification pipeline with MNLI
to assign scene labels to each chunk.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter

from .io_utils import (
    load_timeline, save_timeline, load_canonical_text, 
    save_ml_summary, load_ml_summary, compute_text_hash
)


# Fixed scene labels for classification
SCENE_LABELS = ["conflict", "tension", "movement", "dialogue", "reflection", "wonder"]

# Descriptive hypotheses for better zero-shot NLI discrimination
SCENE_HYPOTHESES = {
    "conflict":   "a confrontation, struggle, or fight between opposing forces",
    "tension":    "suspense, unease, looming danger, or anxious anticipation",
    "movement":   "physical action, motion, travel, or fast-paced events",
    "dialogue":   "a conversation or spoken exchange between characters",
    "reflection": "introspection, internal thoughts, analysis, or contemplation",
    "wonder":     "awe, discovery, mystery, magic, or a sense of the sublime",
}

# Hypothesis template for zero-shot classification
HYPOTHESIS_TEMPLATE = "This passage is best described as {}."

# Build reverse mapping: hypothesis -> scene key
HYPOTHESIS_TO_SCENE = {v: k for k, v in SCENE_HYPOTHESES.items()}

# Thresholds for skipping small chunks
MIN_WORD_COUNT = 40
MIN_CHAR_SPAN = 200

# Truncation for large chunks
MAX_WORDS_FOR_SCORING = 400

# Default model
DEFAULT_MODEL = "facebook/bart-large-mnli"

# Batch size for inference
DEFAULT_BATCH_SIZE = 8

# Progress logging interval
LOG_INTERVAL = 25

# Save interval (save timeline after this many scored chunks)
SAVE_INTERVAL = 5


def compute_config_hash(model: str, labels: List[str]) -> str:
    """Compute hash of ML configuration for reproducibility check."""
    config_str = f"{model}|{'|'.join(sorted(labels))}"
    return hashlib.sha256(config_str.encode('utf-8')).hexdigest()[:16]


def should_skip_chunk(chunk: Dict[str, Any]) -> bool:
    """
    Check if a chunk should be skipped for ML scoring.
    
    Skip if:
    - word_count < MIN_WORD_COUNT (40)
    - OR char span < MIN_CHAR_SPAN (200)
    """
    word_count = chunk.get('word_count', 0)
    start_char = chunk.get('start_char_global', 0)
    end_char = chunk.get('end_char_global', 0)
    char_span = end_char - start_char
    
    return word_count < MIN_WORD_COUNT or char_span < MIN_CHAR_SPAN


def truncate_text_for_scoring(text: str, max_words: int = MAX_WORDS_FOR_SCORING) -> str:
    """
    Truncate text to approximately max_words for faster scoring.
    
    Large chunks (word_count > 450) are truncated to first ~400 words.
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words])


def extract_chunk_text(canonical_text: str, chunk: Dict[str, Any]) -> str:
    """
    Extract chunk text from canonical text using global offsets.
    
    Returns empty string if offsets are invalid or result is whitespace-only.
    """
    start = chunk.get('start_char_global', 0)
    end = chunk.get('end_char_global', 0)
    
    if start >= end or start < 0 or end > len(canonical_text):
        return ""
    
    text = canonical_text[start:end].strip()
    return text


def fill_missing_labels(chunks: List[Dict[str, Any]]) -> int:
    """
    Fill missing scene_label by forward-filling from previous non-null label.
    If first chunk(s) are null, back-fill from next non-null label.
    
    Updates chunks in-place.
    
    Returns:
        Number of chunks that were filled via inheritance
    """
    n = len(chunks)
    if n == 0:
        return 0
    
    inherited_count = 0
    
    # Forward fill
    last_label = None
    for chunk in chunks:
        if chunk.get('scene_label') is not None:
            last_label = chunk['scene_label']
        elif last_label is not None:
            chunk['scene_label'] = last_label
            chunk['scene_source'] = 'inherited_prev'
            inherited_count += 1
    
    # Back fill for leading nulls
    first_label = None
    for chunk in chunks:
        if chunk.get('scene_label') is not None:
            first_label = chunk['scene_label']
            break
    
    if first_label is not None:
        for chunk in chunks:
            if chunk.get('scene_label') is None:
                chunk['scene_label'] = first_label
                chunk['scene_source'] = 'inherited_next'
                inherited_count += 1
            else:
                break  # Stop at first non-null
    
    return inherited_count


def score_chunks_batch(
    classifier,
    texts: List[str],
    chunk_indices: List[int],
) -> List[Tuple[int, List[float], str, float]]:
    """
    Score a batch of texts using the zero-shot classifier with descriptive hypotheses.
    
    Args:
        classifier: HuggingFace zero-shot-classification pipeline
        texts: List of text strings to classify
        chunk_indices: Corresponding chunk indices
        
    Returns:
        List of (chunk_index, scores, scene_key, confidence) tuples
        Scores are aligned with SCENE_LABELS order.
    """
    if not texts:
        return []
    
    # Use descriptive hypotheses for better discrimination
    candidate_labels = [SCENE_HYPOTHESES[k] for k in SCENE_LABELS]
    
    results = classifier(
        texts, 
        candidate_labels, 
        hypothesis_template=HYPOTHESIS_TEMPLATE,
        multi_label=False
    )
    
    # Handle single result (not wrapped in list)
    if isinstance(results, dict):
        results = [results]
    
    output = []
    for idx, result in zip(chunk_indices, results):
        # Map hypothesis labels back to scene keys and build score vector
        hypothesis_to_score = dict(zip(result['labels'], result['scores']))
        scores = [hypothesis_to_score.get(SCENE_HYPOTHESES[scene], 0.0) for scene in SCENE_LABELS]
        
        # Best scene key (map from hypothesis)
        best_hypothesis = result['labels'][0]
        best_scene = HYPOTHESIS_TO_SCENE[best_hypothesis]
        confidence = result['scores'][0]
        
        output.append((idx, scores, best_scene, confidence))
    
    return output


def run_ml_scoring(
    book_dir: Path,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
    device: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run zero-shot NLI scene classification on all chunks.
    
    Args:
        book_dir: Path to the book's data directory
        model_name: HuggingFace model name for zero-shot classification
        batch_size: Batch size for inference
        force: If True, recompute even if matching results exist
        device: Device to run inference on ('cpu', 'cuda', etc.)
        
    Returns:
        ML summary dictionary with statistics
    """
    print(f"Loading timeline from {book_dir}...")
    timeline = load_timeline(book_dir)
    
    # Load canonical text
    canonical_text = load_canonical_text(book_dir)
    if canonical_text is None:
        raise FileNotFoundError(
            f"canonical_text.txt not found in {book_dir}. "
            "Run preprocessing first to generate it."
        )
    
    canonical_hash = compute_text_hash(canonical_text)
    config_hash = compute_config_hash(model_name, SCENE_LABELS)
    
    # Check if we can skip recomputation
    existing_model = timeline.get('ml_model')
    existing_labels = timeline.get('ml_labels')
    existing_text_hash = timeline.get('canonical_text_hash')
    
    if not force and existing_model == model_name and existing_labels == SCENE_LABELS and existing_text_hash == canonical_hash:
        print("ML results already exist with matching configuration. Use --force to recompute.")
        return load_ml_summary(book_dir) or {}
    
    # Initialize classifier
    print(f"Loading zero-shot classifier: {model_name}...")
    from transformers import pipeline
    
    classifier_kwargs = {"model": model_name}
    if device is not None:
        classifier_kwargs["device"] = device
    
    classifier = pipeline("zero-shot-classification", **classifier_kwargs)
    
    chunks = timeline.get('chunks', [])
    total_chunks = len(chunks)
    print(f"Processing {total_chunks} chunks...")
    
    # Prepare batches - check for already scored chunks (resume support)
    texts_to_score = []
    indices_to_score = []
    skipped_indices = []
    already_scored = 0
    
    for i, chunk in enumerate(chunks):
        # Check if already scored (resume support)
        if not force and chunk.get('scene_source') == 'nli_zero_shot':
            already_scored += 1
            continue
        
        # Check if should skip
        if should_skip_chunk(chunk):
            skipped_indices.append(i)
            chunk['scene_label'] = None
            chunk['scene_scores'] = None
            chunk['scene_conf'] = None
            chunk['scene_source'] = 'skipped_small'
            continue
        
        # Extract text
        text = extract_chunk_text(canonical_text, chunk)
        
        # Check if text is empty/whitespace
        if not text or not text.strip():
            skipped_indices.append(i)
            chunk['scene_label'] = None
            chunk['scene_scores'] = None
            chunk['scene_conf'] = None
            chunk['scene_source'] = 'skipped_small'
            continue
        
        # Truncate large chunks
        word_count = chunk.get('word_count', len(text.split()))
        if word_count > 450:
            text = truncate_text_for_scoring(text)
        
        texts_to_score.append(text)
        indices_to_score.append(i)
    
    if already_scored > 0:
        print(f"Resuming: {already_scored} chunks already scored, {len(texts_to_score)} remaining")
    
    print(f"Scoring {len(texts_to_score)} chunks, skipping {len(skipped_indices)} small chunks...")
    
    if len(texts_to_score) == 0:
        print("All chunks already scored. Use --force to recompute.")
        # Still fill inherited and generate summary
        inherited_count = fill_missing_labels(chunks)
        return _finalize_and_save(book_dir, timeline, chunks, model_name, config_hash, canonical_hash, 
                                  already_scored, len(skipped_indices), inherited_count)
    
    # Process in batches with incremental saves
    scored_count = 0
    total_confidence = 0.0
    last_save_count = 0
    
    for batch_start in range(0, len(texts_to_score), batch_size):
        batch_end = min(batch_start + batch_size, len(texts_to_score))
        batch_texts = texts_to_score[batch_start:batch_end]
        batch_indices = indices_to_score[batch_start:batch_end]
        
        results = score_chunks_batch(classifier, batch_texts, batch_indices)
        
        for chunk_idx, scores, label, confidence in results:
            chunks[chunk_idx]['scene_scores'] = [round(s, 4) for s in scores]
            chunks[chunk_idx]['scene_label'] = label
            chunks[chunk_idx]['scene_conf'] = round(confidence, 4)
            chunks[chunk_idx]['scene_source'] = 'nli_zero_shot'
            
            scored_count += 1
            total_confidence += confidence
        
        # Incremental save after every SAVE_INTERVAL scored chunks
        if scored_count - last_save_count >= SAVE_INTERVAL:
            save_timeline(book_dir, timeline)
            last_save_count = scored_count
            print(f"  [saved progress: {scored_count + already_scored} chunks]")
        
        # Progress logging
        processed = batch_end
        if processed % LOG_INTERVAL == 0 or processed == len(texts_to_score):
            avg_conf = total_confidence / scored_count if scored_count > 0 else 0.0
            print(f"  Processed: {processed}/{len(texts_to_score)}, "
                  f"Skipped: {len(skipped_indices)}, "
                  f"Avg conf: {avg_conf:.3f}")
    
    # Fill missing labels via inheritance
    inherited_count = fill_missing_labels(chunks)
    
    # Include already_scored in total
    total_scored = scored_count + already_scored
    
    # Compute label histogram
    label_counts = Counter(c.get('scene_label') for c in chunks if c.get('scene_label'))
    
    # Compute stats (only for this run's scored chunks)
    mean_confidence = total_confidence / scored_count if scored_count > 0 else 0.0
    
    # Update timeline with ML metadata
    timeline['ml_model'] = model_name
    timeline['ml_labels'] = SCENE_LABELS
    timeline['ml_run_timestamp'] = datetime.now().isoformat()
    timeline['ml_config_hash'] = config_hash
    timeline['canonical_text_hash'] = canonical_hash
    
    # Save updated timeline
    save_timeline(book_dir, timeline)
    print(f"Updated timeline.json with scene labels")
    
    # Create summary
    summary = {
        'book_id': timeline.get('book_id'),
        'ml_model': model_name,
        'ml_labels': SCENE_LABELS,
        'ml_run_timestamp': timeline['ml_run_timestamp'],
        'total_chunks': total_chunks,
        'scored_chunks': total_scored,
        'skipped_small': len(skipped_indices),
        'inherited_labels': inherited_count,
        'mean_confidence': round(mean_confidence, 4),
        'label_histogram': dict(label_counts),
        'canonical_text_hash': canonical_hash
    }
    
    # Save summary
    save_ml_summary(book_dir, summary)
    print(f"Saved ml_summary.json")
    
    # Print final stats
    print(f"\n=== ML Scoring Complete ===")
    print(f"  Total chunks:     {total_chunks}")
    print(f"  Scored chunks:    {total_scored}")
    if already_scored > 0:
        print(f"    (resumed:       {already_scored})")
        print(f"    (this run:      {scored_count})")
    print(f"  Skipped (small):  {len(skipped_indices)}")
    print(f"  Inherited labels: {inherited_count}")
    print(f"  Mean confidence:  {mean_confidence:.4f}")
    print(f"\n  Label distribution:")
    for label in SCENE_LABELS:
        count = label_counts.get(label, 0)
        pct = count / total_chunks * 100 if total_chunks > 0 else 0
        print(f"    {label:12s}: {count:4d} ({pct:5.1f}%)")
    
    return summary


def _finalize_and_save(
    book_dir: Path,
    timeline: Dict[str, Any],
    chunks: List[Dict[str, Any]],
    model_name: str,
    config_hash: str,
    canonical_hash: str,
    scored_count: int,
    skipped_count: int,
    inherited_count: int
) -> Dict[str, Any]:
    """Helper to finalize and save ML results."""
    total_chunks = len(chunks)
    label_counts = Counter(c.get('scene_label') for c in chunks if c.get('scene_label'))
    
    # Compute mean confidence from scored chunks
    confidences = [c.get('scene_conf', 0) for c in chunks if c.get('scene_source') == 'nli_zero_shot']
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    # Update timeline metadata
    timeline['ml_model'] = model_name
    timeline['ml_labels'] = SCENE_LABELS
    timeline['ml_run_timestamp'] = datetime.now().isoformat()
    timeline['ml_config_hash'] = config_hash
    timeline['canonical_text_hash'] = canonical_hash
    
    save_timeline(book_dir, timeline)
    
    summary = {
        'book_id': timeline.get('book_id'),
        'ml_model': model_name,
        'ml_labels': SCENE_LABELS,
        'ml_run_timestamp': timeline['ml_run_timestamp'],
        'total_chunks': total_chunks,
        'scored_chunks': scored_count,
        'skipped_small': skipped_count,
        'inherited_labels': inherited_count,
        'mean_confidence': round(mean_confidence, 4),
        'label_histogram': dict(label_counts),
        'canonical_text_hash': canonical_hash
    }
    
    save_ml_summary(book_dir, summary)
    
    print(f"\n=== ML Scoring Complete ===")
    print(f"  Total chunks:     {total_chunks}")
    print(f"  Scored chunks:    {scored_count}")
    print(f"  Skipped (small):  {skipped_count}")
    print(f"  Inherited labels: {inherited_count}")
    print(f"  Mean confidence:  {mean_confidence:.4f}")
    print(f"\n  Label distribution:")
    for label in SCENE_LABELS:
        count = label_counts.get(label, 0)
        pct = count / total_chunks * 100 if total_chunks > 0 else 0
        print(f"    {label:12s}: {count:4d} ({pct:5.1f}%)")
    
    return summary


def score_ml_from_book_id(
    book_id: str,
    data_dir: str = "data",
    model_name: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
    device: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to run ML scoring by book_id.
    
    Args:
        book_id: Book identifier
        data_dir: Base data directory
        model_name: HuggingFace model name
        batch_size: Batch size for inference
        force: If True, recompute even if matching results exist
        device: Device for inference
        
    Returns:
        ML summary dictionary
    """
    book_dir = Path(data_dir) / book_id
    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    
    return run_ml_scoring(
        book_dir=book_dir,
        model_name=model_name,
        batch_size=batch_size,
        force=force,
        device=device
    )


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python ml_scene.py <book_id> [--force]")
        sys.exit(1)
    
    book_id = sys.argv[1]
    force = '--force' in sys.argv
    
    try:
        summary = score_ml_from_book_id(book_id, force=force)
        print(f"\nDone! Summary saved to data/{book_id}/ml_summary.json")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
