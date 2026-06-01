"""Calibre-native adaptive ambience core."""

from .calibre_native import (
    CalibreBook,
    LiveAnnotation,
    compute_annots_key,
    default_calibre_annots_dir,
    find_book,
    find_book_by_annots_key,
    import_calibre_library,
    newest_live_annotation,
    read_live_annotation_for_book,
)
from .anchors import (
    build_anchors_from_spine,
    find_anchor_for_position,
    prepare_book_timeline,
)

__all__ = [
    "CalibreBook",
    "LiveAnnotation",
    "compute_annots_key",
    "default_calibre_annots_dir",
    "find_book",
    "find_book_by_annots_key",
    "import_calibre_library",
    "newest_live_annotation",
    "read_live_annotation_for_book",
    "build_anchors_from_spine",
    "find_anchor_for_position",
    "prepare_book_timeline",
]
