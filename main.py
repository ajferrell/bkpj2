"""
EPUB Ambience Orchestrator (v1)
Main entry point and orchestration logic.

Updated to support exact CFI-to-chunk mapping via Calibre's CFI parser.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from src.audio_engine import AudioEngine
from src.controller import Controller
from src.watcher import CalibreWatcher
from src.resolver_calibre import CalibreCFIResolver
from src.logger import OrchestratorLogger
from src.preprocessor import preprocess_epub


def find_timeline_for_calibre_id(calibre_id: str, data_dir: Path, book_id_mapping: dict) -> Optional[Path]:
    """
    Find the timeline.json for a Calibre annotation ID.
    
    Search order:
    1. Check book_id_mapping in config
    2. Search all timelines for matching calibre_id field
    3. Try calibre_id as direct book_id
    
    Returns the timeline path if found, None otherwise.
    """
    # 1. Check explicit mapping
    if calibre_id in book_id_mapping:
        mapped_id = book_id_mapping[calibre_id]
        timeline_path = data_dir / mapped_id / "timeline.json"
        if timeline_path.exists():
            return timeline_path
    
    # 2. Search all timelines for calibre_id field
    if data_dir.exists():
        for subdir in data_dir.iterdir():
            if subdir.is_dir():
                timeline_path = subdir / "timeline.json"
                if timeline_path.exists():
                    try:
                        with open(timeline_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        # Check if this timeline has a calibre_id that matches
                        if data.get('calibre_id') == calibre_id:
                            return timeline_path
                    except Exception:
                        continue
    
    # 3. Try calibre_id as direct book_id
    timeline_path = data_dir / calibre_id / "timeline.json"
    if timeline_path.exists():
        return timeline_path
    
    return None


def link_calibre_to_book(calibre_id: str, book_id: str, data_dir: Path) -> bool:
    """
    Link a Calibre annotation ID to a preprocessed book by storing it in timeline.
    
    Returns True if successful.
    """
    timeline_path = data_dir / book_id / "timeline.json"
    if not timeline_path.exists():
        print(f"Error: No timeline found for book_id '{book_id}'")
        return False
    
    try:
        with open(timeline_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        data['calibre_id'] = calibre_id
        
        with open(timeline_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"Linked Calibre ID '{calibre_id}' to book '{book_id}'")
        return True
    except Exception as e:
        print(f"Error linking: {e}")
        return False


class Orchestrator:
    """
    Main orchestrator that coordinates all components.
    """
    
    def __init__(self, config_path: str = "config.json"):
        # Load config
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        # Components
        self.logger = OrchestratorLogger()
        self.audio_engine: Optional[AudioEngine] = None
        self.controller: Optional[Controller] = None
        self.watcher: Optional[CalibreWatcher] = None
        self.resolver: Optional[CalibreCFIResolver] = None
        
        # Scene list
        self.scene_list = list(self.config['scene_bins'].keys())
        
        print("Orchestrator initialized")
    
    def run(self, dummy: bool = False, book_id: Optional[str] = None):
        """
        Run the orchestrator.
        
        Args:
            dummy: Run in dummy mode (cycles through scenes without Calibre)
            book_id: Specific book ID to watch (otherwise uses most recent)
        """
        if dummy:
            self._run_dummy_mode()
        else:
            self._run_live_mode(book_id)
    
    def _run_dummy_mode(self):
        """Run in dummy mode: cycle through chunks without Calibre."""
        print("\n=== DUMMY MODE ===")
        print("Cycling through chunks without Calibre integration\n")
        
        # Initialize audio engine
        self.audio_engine = AudioEngine(
            scene_bins=self.config['scene_bins'],
            crossfade_duration=self.config['crossfade_duration_sec'],
            fade_duration=self.config['fade_duration_sec'],
            device=self.config.get('audio_device')
        )
        
        # Initialize controller
        self.controller = Controller(
            scene_list=self.scene_list,
            dwell_time_sec=self.config['dwell_time_sec'],
            logger=self.logger
        )
        
        # Start audio
        self.audio_engine.start()
        
        try:
            chunk_id = 0
            cycle_time = self.config.get('dummy_cycle_sec', 10)
            
            while True:
                # Simulate reading progress
                confidence = 0.85  # Dummy confidence
                
                # Update controller
                target_scene = self.controller.update(
                    chunk_id=chunk_id,
                    confidence=confidence,
                    book_id="dummy_book"
                )
                
                # Play scene if controller says so
                if target_scene:
                    current_scene = self.audio_engine.get_current_scene()
                    if current_scene != target_scene:
                        self.audio_engine.play_scene(target_scene)
                
                # Update status
                self.logger.update_status(
                    book_id="dummy_book",
                    chunk_id=chunk_id,
                    total_chunks=100,  # Dummy total
                    confidence=confidence
                )
                
                # Sleep
                time.sleep(cycle_time)
                
                # Next chunk
                chunk_id += 1
        
        except KeyboardInterrupt:
            print("\n\nStopping...")
        
        finally:
            self.audio_engine.stop()
    
    def _run_live_mode(self, book_id: Optional[str] = None):
        """Run in live mode: watch Calibre and play ambience."""
        print("\n=== LIVE MODE ===")
        print("Watching Calibre for reading progress\n")
        
        # Initialize components
        self.audio_engine = AudioEngine(
            scene_bins=self.config['scene_bins'],
            crossfade_duration=self.config['crossfade_duration_sec'],
            fade_duration=self.config['fade_duration_sec'],
            device=self.config.get('audio_device')
        )
        
        self.watcher = CalibreWatcher(
            annots_path=self.config.get('calibre_annots_path')
        )
        
        # Start watcher
        self.watcher.start()
        
        # Start audio
        self.audio_engine.start()
        
        try:
            current_book_id = None
            poll_interval = 1.0  # Poll every second
            
            while True:
                # Poll for new position
                position = self.watcher.poll()
                
                if position:
                    calibre_id = position['book_id']  # This is Calibre's internal hash
                    epubcfi = position['epubcfi']
                    
                    # Find timeline for this Calibre ID
                    book_id_mapping = self.config.get('book_id_mapping', {})
                    timeline_path = find_timeline_for_calibre_id(
                        calibre_id, 
                        Path("data"), 
                        book_id_mapping
                    )
                    
                    if timeline_path is None:
                        if calibre_id != current_book_id:  # Only warn once per book
                            self.logger.print_message(
                                f"\nNo timeline found for Calibre ID: {calibre_id[:16]}...\n"
                                f"  To link: python main.py link {calibre_id} <book_id>\n"
                                f"  Or preprocess the EPUB first: python main.py preprocess <epub_path>"
                            )
                            current_book_id = calibre_id
                        continue
                    
                    # Get book_id from timeline
                    with open(timeline_path, 'r', encoding='utf-8') as f:
                        timeline_data = json.load(f)
                    book_id = timeline_data.get('book_id', 'unknown')
                    
                    # Check if book changed
                    if book_id != current_book_id:
                        self.logger.print_message(f"\nSwitched to book: {book_id} (Calibre: {calibre_id[:16]}...)")
                        current_book_id = book_id
                        
                        epub_path = timeline_data.get('epub_path')
                        
                        if not epub_path or not Path(epub_path).exists():
                            self.logger.print_message(f"Warning: EPUB not found: {epub_path}")
                            current_book_id = None
                            continue
                        
                        # Auto-link Calibre ID to this book for future lookups
                        if timeline_data.get('calibre_id') != calibre_id:
                            link_calibre_to_book(calibre_id, book_id, Path("data"))
                        
                        # Initialize exact CFI resolver (uses Calibre's parser)
                        self.resolver = CalibreCFIResolver(epub_path, str(timeline_path))
                        
                        # Initialize controller
                        self.controller = Controller(
                            scene_list=self.scene_list,
                            dwell_time_sec=self.config['dwell_time_sec'],
                            logger=self.logger
                        )
                        
                        # Set total chunks for percentage display
                        total_chunks = timeline_data.get('total_chunks')
                        if total_chunks:
                            self.controller.set_total_chunks(total_chunks)
                        
                        self.logger.update_status(book_id=book_id, total_chunks=total_chunks)
                    
                    # Resolve CFI to chunk using exact resolver
                    if self.resolver:
                        result = self.resolver.resolve(epubcfi)
                        
                        if result is not None:
                            chunk_id = result.chunk_id
                            confidence = result.confidence
                            
                            # Update controller
                            target_scene = self.controller.update(
                                chunk_id=chunk_id,
                                confidence=confidence,
                                book_id=current_book_id
                            )
                            
                            # Play scene if controller says so
                            if target_scene:
                                current_scene = self.audio_engine.get_current_scene()
                                if current_scene != target_scene:
                                    self.audio_engine.play_scene(target_scene)
                
                # Sleep
                time.sleep(poll_interval)
        
        except KeyboardInterrupt:
            print("\n\nStopping...")
        
        finally:
            self.audio_engine.stop()
            self.watcher.stop()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="EPUB Ambience Orchestrator v1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  preprocess <epub_path>    Preprocess an EPUB file
  run [--dummy]             Run the orchestrator
  link <calibre_id> <book_id>  Link Calibre annots ID to a book
  list                      List all preprocessed books

Examples:
  python main.py preprocess mybook.epub
  python main.py run
  python main.py run --dummy
  python main.py link abc123def B0C5S477SF
  python main.py list
        """
    )
    
    parser.add_argument(
        'command',
        choices=['preprocess', 'run', 'link', 'list'],
        help='Command to execute'
    )
    
    parser.add_argument(
        'args',
        nargs='*',
        help='Command arguments'
    )
    
    parser.add_argument(
        '--dummy',
        action='store_true',
        help='Run in dummy mode (cycles through scenes)'
    )
    
    parser.add_argument(
        '--book-id',
        type=str,
        help='Specific book ID to watch'
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config.json',
        help='Path to config file (default: config.json)'
    )
    
    args = parser.parse_args()
    
    # Execute command
    if args.command == 'preprocess':
        if not args.args:
            print("Error: epub_path required for preprocess command")
            print("Usage: python main.py preprocess <epub_path>")
            sys.exit(1)
        
        epub_path = args.args[0]
        print(f"Preprocessing: {epub_path}\n")
        timeline_path = preprocess_epub(epub_path)
        print(f"\nDone! Timeline saved to: {timeline_path}")
    
    elif args.command == 'link':
        if len(args.args) < 2:
            print("Error: calibre_id and book_id required for link command")
            print("Usage: python main.py link <calibre_id> <book_id>")
            print("\nTo find your Calibre ID, run the orchestrator and it will show")
            print("the ID when it detects a book without a linked timeline.")
            sys.exit(1)
        
        calibre_id = args.args[0]
        book_id = args.args[1]
        
        if link_calibre_to_book(calibre_id, book_id, Path("data")):
            print(f"\nSuccess! Calibre ID '{calibre_id}' is now linked to book '{book_id}'")
        else:
            sys.exit(1)
    
    elif args.command == 'list':
        data_dir = Path("data")
        if not data_dir.exists():
            print("No preprocessed books found (data/ directory doesn't exist)")
            sys.exit(0)
        
        print("\nPreprocessed books:\n")
        for subdir in sorted(data_dir.iterdir()):
            if subdir.is_dir():
                timeline_path = subdir / "timeline.json"
                if timeline_path.exists():
                    try:
                        with open(timeline_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        book_id = data.get('book_id', subdir.name)
                        calibre_id = data.get('calibre_id', '(not linked)')
                        epub_path = data.get('epub_path', '?')
                        chunks = data.get('total_chunks', '?')
                        print(f"  {book_id}")
                        print(f"    Calibre ID: {calibre_id}")
                        print(f"    Chunks: {chunks}")
                        print(f"    EPUB: {epub_path}")
                        print()
                    except Exception as e:
                        print(f"  {subdir.name}: (error reading timeline: {e})")
    
    elif args.command == 'run':
        orchestrator = Orchestrator(config_path=args.config)
        orchestrator.run(dummy=args.dummy, book_id=args.book_id)


if __name__ == '__main__':
    main()
