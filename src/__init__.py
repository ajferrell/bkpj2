"""
EPUB Ambience Orchestrator - Core modules.
"""

from .audio_engine import AudioEngine
from .controller import Controller
from .logger import OrchestratorLogger
from .watcher import CalibreWatcher
from .preprocessor import preprocess_epub
from .resolver_calibre import CalibreCFIResolver
from .chunk_index import ChunkIndex, load_chunk_index

__all__ = [
    'AudioEngine',
    'Controller', 
    'OrchestratorLogger',
    'CalibreWatcher',
    'preprocess_epub',
    'CalibreCFIResolver',
    'ChunkIndex',
    'load_chunk_index',
]
