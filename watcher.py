r"""
Calibre annotations watcher.
Monitors %APPDATA%\calibre\viewer\annots\*.json for reading progress.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class CalibreWatcher:
    """
    Watches Calibre's annotations directory for reading progress updates.
    Extracts the most recent last-read position.
    """
    
    def __init__(self, annots_path: Optional[str] = None):
        # Resolve %APPDATA%
        if annots_path is None:
            appdata = os.environ.get('APPDATA', '')
            annots_path = os.path.join(appdata, 'calibre', 'viewer', 'annots')
        else:
            annots_path = os.path.expandvars(annots_path)
        
        self.annots_path = Path(annots_path)
        self.observer = None
        self.last_position: Optional[Dict[str, Any]] = None
        self.last_file: Optional[Path] = None
        self.last_mtime = 0.0
        
        print(f"Watching Calibre annots: {self.annots_path}")
    
    def start(self):
        """Start watching the annotations directory."""
        if not self.annots_path.exists():
            print(f"Warning: Calibre annots path does not exist: {self.annots_path}")
            print("Creating directory...")
            self.annots_path.mkdir(parents=True, exist_ok=True)
        
        # Initial scan
        self._scan_for_updates()
        
        # Start file watcher
        event_handler = _CalibreEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.annots_path), recursive=False)
        self.observer.start()
        
        print("Calibre watcher started")
    
    def stop(self):
        """Stop the watcher."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        
        print("Calibre watcher stopped")
    
    def poll(self) -> Optional[Dict[str, Any]]:
        """
        Poll for new reading position.
        Returns a dict with 'epubcfi', 'book_id', 'timestamp' if updated, else None.
        """
        self._scan_for_updates()
        
        if self.last_position:
            # Return and clear
            pos = self.last_position
            self.last_position = None
            return pos
        
        return None
    
    def _scan_for_updates(self):
        """Scan all JSON files for the most recent update."""
        if not self.annots_path.exists():
            return
        
        # Find most recently modified JSON file
        json_files = list(self.annots_path.glob('*.json'))
        
        if not json_files:
            return
        
        # Sort by modification time
        json_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        newest = json_files[0]
        newest_mtime = newest.stat().st_mtime
        
        # Check if this is new (compare paths as strings to be safe)
        if self.last_file and str(newest) == str(self.last_file) and newest_mtime <= self.last_mtime:
            return
        
        # File has been modified, parse it
        print(f"Detected update: {newest.name} (mtime: {newest_mtime})")
        position = self._parse_annots_file(newest)
        
        if position:
            self.last_position = position
            self.last_file = newest
            self.last_mtime = newest_mtime
            print(f"  -> Found position for book: {position['book_id'][:16]}...")
        else:
            print(f"  -> No valid position found in this file")
    
    def _parse_annots_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """
        Parse a Calibre annotations JSON file.
        Extract the most recent last-read position.
        Handles both dict and list formats.
        """
        try:
            # Read file, handling potential partial writes
            content = self._read_json_robustly(path)
            
            if not content:
                return None
            
            # Find last-read entries
            last_read = None
            last_timestamp = 0
            
            # Handle both list and dict formats
            entries = []
            
            if isinstance(content, list):
                entries = content
            elif isinstance(content, dict):
                entries = list(content.values()) if content else []
            
            for entry in entries:
                if isinstance(entry, dict):
                    entry_type = entry.get('type')
                    pos_type = entry.get('pos_type')
                    timestamp = entry.get('timestamp', 0)
                    
                    # Convert timestamp to float for comparison
                    try:
                        if isinstance(timestamp, str):
                            # Try parsing as ISO 8601 datetime
                            try:
                                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                timestamp_val = dt.timestamp()
                            except:
                                # Try as numeric string
                                timestamp_val = float(timestamp)
                        else:
                            timestamp_val = float(timestamp) if timestamp else 0
                    except (ValueError, TypeError):
                        timestamp_val = 0
                    
                    if entry_type == 'last-read' and pos_type == 'epubcfi':
                        if timestamp_val > last_timestamp:
                            last_read = entry
                            last_timestamp = timestamp_val
            
            if last_read:
                epubcfi = last_read.get('pos')
                
                if epubcfi:
                    # Extract book_id from filename (usually <book_id>.json)
                    book_id = path.stem
                    
                    return {
                        'epubcfi': epubcfi,
                        'book_id': book_id,
                        'timestamp': last_timestamp
                    }
        
        except Exception as e:
            print(f"Warning: Failed to parse {path}: {e}")
        
        return None
    
    def _read_json_robustly(self, path: Path) -> Optional[Dict]:
        """
        Read JSON file with retry logic to handle partial writes.
        """
        for attempt in range(3):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Try to parse
                data = json.loads(content)
                return data
            
            except json.JSONDecodeError:
                # Partial write, retry
                if attempt < 2:
                    time.sleep(0.1)
                else:
                    return None
            
            except Exception as e:
                print(f"Error reading {path}: {e}")
                return None
        
        return None


class _CalibreEventHandler(FileSystemEventHandler):
    """Internal event handler for watchdog."""
    
    def __init__(self, watcher: CalibreWatcher):
        self.watcher = watcher
    
    def on_modified(self, event):
        """File modified event."""
        if event.is_directory:
            return
        
        if event.src_path.endswith('.json'):
            # Trigger scan
            self.watcher._scan_for_updates()
    
    def on_created(self, event):
        """File created event."""
        if event.is_directory:
            return
        
        if event.src_path.endswith('.json'):
            # Trigger scan
            self.watcher._scan_for_updates()


def test_watcher():
    """Test the Calibre watcher."""
    watcher = CalibreWatcher()
    watcher.start()
    
    try:
        print("\nWatching for Calibre updates... (Ctrl+C to stop)\n")
        
        while True:
            pos = watcher.poll()
            
            if pos:
                print(f"\nNew position:")
                print(f"  Book: {pos['book_id']}")
                print(f"  CFI: {pos['epubcfi']}")
                print(f"  Time: {pos['timestamp']}\n")
            
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        watcher.stop()


if __name__ == '__main__':
    test_watcher()

# command to activate virtual environment in terminal powershell:
