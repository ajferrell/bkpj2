"""
Logging and console output for the orchestrator.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


class OrchestratorLogger:
    """
    Handles JSONL logging and console status updates.
    """
    
    def __init__(self, log_file: str = "orchestrator.jsonl"):
        self.log_file = Path(log_file)
        self.current_status = {
            'book_id': None,
            'chunk_id': None,
            'total_chunks': None,
            'scene': None,
            'active_bin': None,
            'dwell_remaining': None,
            'confidence': None
        }
    
    def log_event(
        self,
        epubcfi: Optional[str] = None,
        chunk_id: Optional[int] = None,
        confidence: Optional[float] = None,
        target_scene: Optional[str] = None,
        active_scene: Optional[str] = None,
        reason: str = ""
    ):
        """Write a JSONL log entry."""
        entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'epubcfi': epubcfi,
            'chunk_id': chunk_id,
            'confidence': confidence,
            'target_scene': target_scene,
            'active_scene': active_scene,
            'reason': reason
        }
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    
    def update_status(
        self,
        book_id: Optional[str] = None,
        chunk_id: Optional[int] = None,
        total_chunks: Optional[int] = None,
        scene: Optional[str] = None,
        active_bin: Optional[str] = None,
        dwell_remaining: Optional[float] = None,
        confidence: Optional[float] = None
    ):
        """Update status and refresh console display."""
        # Update fields
        if book_id is not None:
            self.current_status['book_id'] = book_id
        if chunk_id is not None:
            self.current_status['chunk_id'] = chunk_id
        if total_chunks is not None:
            self.current_status['total_chunks'] = total_chunks
        if scene is not None:
            self.current_status['scene'] = scene
        if active_bin is not None:
            self.current_status['active_bin'] = active_bin
        if dwell_remaining is not None:
            self.current_status['dwell_remaining'] = dwell_remaining
        if confidence is not None:
            self.current_status['confidence'] = confidence
        
        # Format status line
        status_line = self._format_status_line()
        
        # Clear line and print
        sys.stdout.write('\r' + ' ' * 120 + '\r')
        sys.stdout.write(status_line)
        sys.stdout.flush()
    
    def _format_status_line(self) -> str:
        """Format the current status as a console line."""
        s = self.current_status
        
        parts = []
        
        if s['book_id']:
            parts.append(f"book={s['book_id']}")
        
        if s['chunk_id'] is not None:
            total = s.get('total_chunks')
            if total:
                pct = (s['chunk_id'] / total) * 100
                parts.append(f"chunk={s['chunk_id']}/{total} ({pct:.1f}%)")
            else:
                parts.append(f"chunk={s['chunk_id']}")
        
        if s['scene']:
            parts.append(f"scene={s['scene']}")
        
        if s['active_bin']:
            parts.append(f"active={s['active_bin']}")
        
        if s['dwell_remaining'] is not None:
            parts.append(f"dwell={s['dwell_remaining']:.0f}s")
        
        if s['confidence'] is not None:
            parts.append(f"conf={s['confidence']:.2f}")
        
        return " | ".join(parts) if parts else "Idle"
    
    def print_message(self, message: str):
        """Print a message, preserving the status line."""
        # Clear status line
        sys.stdout.write('\r' + ' ' * 120 + '\r')
        
        # Print message
        print(message)
        
        # Restore status line
        sys.stdout.write(self._format_status_line())
        sys.stdout.flush()
