"""
EPUB Ambience Orchestrator (v1)
Main entry point and orchestration logic.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from audio_engine import AudioEngine
from controller import Controller
from watcher import CalibreWatcher
from resolver import CFIResolver
from logger import OrchestratorLogger
from preprocessor import preprocess_epub


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
        self.resolver: Optional[CFIResolver] = None
        
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
                    new_book_id = position['book_id']
                    epubcfi = position['epubcfi']
                    
                    # Map Calibre hash to actual book ID if configured
                    book_id_mapping = self.config.get('book_id_mapping', {})
                    mapped_book_id = book_id_mapping.get(new_book_id, new_book_id)
                    
                    # Check if book changed
                    if mapped_book_id != current_book_id:
                        self.logger.print_message(f"\nSwitched to book: {mapped_book_id} (Calibre: {new_book_id[:16]}...)")
                        current_book_id = mapped_book_id
                        
                        # Load timeline for this book
                        timeline_path = Path("data") / mapped_book_id / "timeline.json"
                        
                        if not timeline_path.exists():
                            self.logger.print_message(
                                f"Warning: No timeline found for '{mapped_book_id}'. "
                                f"Run: python main.py preprocess <epub_path>"
                            )
                            current_book_id = None
                            continue
                        
                        # Initialize resolver
                        self.resolver = CFIResolver(str(timeline_path))
                        
                        # Initialize controller
                        self.controller = Controller(
                            scene_list=self.scene_list,
                            dwell_time_sec=self.config['dwell_time_sec'],
                            logger=self.logger
                        )
                        
                        self.logger.update_status(book_id=new_book_id)
                    
                    # Resolve CFI to chunk
                    if self.resolver:
                        chunk_id, confidence = self.resolver.resolve(epubcfi)
                        
                        if chunk_id is not None:
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

Examples:
  python main.py preprocess mybook.epub
  python main.py run
  python main.py run --dummy
        """
    )
    
    parser.add_argument(
        'command',
        choices=['preprocess', 'run'],
        help='Command to execute'
    )
    
    parser.add_argument(
        'epub_path',
        nargs='?',
        help='Path to EPUB file (for preprocess command)'
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
        if not args.epub_path:
            print("Error: epub_path required for preprocess command")
            parser.print_help()
            sys.exit(1)
        
        print(f"Preprocessing: {args.epub_path}\n")
        timeline_path = preprocess_epub(args.epub_path)
        print(f"\nDone! Timeline saved to: {timeline_path}")
    
    elif args.command == 'run':
        orchestrator = Orchestrator(config_path=args.config)
        orchestrator.run(dummy=args.dummy, book_id=args.book_id)


if __name__ == '__main__':
    main()
