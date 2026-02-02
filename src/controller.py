"""
Controller: state machine and anti-thrash logic.
"""

import time
from enum import Enum
from typing import Optional, List
from .logger import OrchestratorLogger


class ControllerState(Enum):
    """Controller state machine states."""
    IDLE = "idle"
    TRACKING = "tracking"
    PLAYING = "playing"
    SWITCH_PENDING = "switch_pending"


class Controller:
    """
    Manages scene switching with anti-thrash logic.
    
    State transitions:
    - IDLE → TRACKING: first chunk received
    - TRACKING → PLAYING: K consecutive chunks confirmed
    - PLAYING → SWITCH_PENDING: new scene detected
    - SWITCH_PENDING → PLAYING: dwell time elapsed + K consecutive
    """
    
    def __init__(
        self,
        scene_list: List[str],
        dwell_time_sec: float = 120.0,
        k_consecutive: int = 0,
        logger: Optional[OrchestratorLogger] = None,
        timeline: Optional[dict] = None
    ):
        self.scene_list = scene_list
        self.dwell_time_sec = dwell_time_sec
        self.k_consecutive = k_consecutive
        self.logger = logger or OrchestratorLogger()
        self.timeline = timeline  # Timeline data with chunks
        
        # State
        self.state = ControllerState.IDLE
        self.current_scene: Optional[str] = None
        self.target_scene: Optional[str] = None
        self.total_chunks: Optional[int] = None  # For % display
        
        # Anti-thrash tracking
        self.chunk_history: List[int] = []
        self.last_switch_time = 0.0
        self.consecutive_count = 0
        self.last_chunk_id: Optional[int] = None
    
    def set_total_chunks(self, total: int):
        """Set total chunks for percentage display."""
        self.total_chunks = total
    
    def set_timeline(self, timeline: dict):
        """Set timeline data for scene label lookup."""
        self.timeline = timeline
    
    def update(
        self,
        chunk_id: int,
        confidence: float,
        book_id: str = "unknown"
    ) -> Optional[str]:
        """
        Update controller with new chunk information.
        Returns the scene that should be playing (or None).
        """
        current_time = time.time()
        
        # Derive target scene from chunk_id (dummy assignment)
        target_scene = self._get_scene_for_chunk(chunk_id)
        
        # Track consecutive chunks
        if chunk_id != self.last_chunk_id:
            if self.last_chunk_id is not None:
                # Check if consecutive
                if abs(chunk_id - self.last_chunk_id) <= 1:
                    self.consecutive_count += 1
                else:
                    # Jump detected, reset
                    self.consecutive_count = 1
                    if confidence < 0.6:
                        # Low confidence jump, ignore
                        self.logger.log_event(
                            chunk_id=chunk_id,
                            confidence=confidence,
                            target_scene=target_scene,
                            active_scene=self.current_scene,
                            reason="low_confidence_jump_ignored"
                        )
                        return self.current_scene
            else:
                # First chunk ever
                self.consecutive_count = 1
            
            self.last_chunk_id = chunk_id
        # If same chunk_id as last time, don't update consecutive_count
        # (we're still in the same position)
        
        # Update chunk history
        self.chunk_history.append(chunk_id)
        if len(self.chunk_history) > self.k_consecutive * 2:
            self.chunk_history = self.chunk_history[-self.k_consecutive * 2:]
        
        # State machine
        prev_state = self.state
        result = None
        
        if self.state == ControllerState.IDLE:
            result = self._handle_idle(chunk_id, target_scene, confidence, book_id)
        
        elif self.state == ControllerState.TRACKING:
            result = self._handle_tracking(chunk_id, target_scene, confidence, book_id)
        
        elif self.state == ControllerState.PLAYING:
            result = self._handle_playing(
                chunk_id, target_scene, confidence, book_id, current_time
            )
        
        elif self.state == ControllerState.SWITCH_PENDING:
            result = self._handle_switch_pending(
                chunk_id, target_scene, confidence, book_id, current_time
            )
        
        # Log state transitions
        if self.state != prev_state:
            transition_msg = f"[STATE] {prev_state.value} -> {self.state.value}"
            print(f"\n{transition_msg}")
            self.logger.log_event(
                chunk_id=chunk_id,
                confidence=confidence,
                target_scene=target_scene,
                active_scene=self.current_scene,
                reason=f"state_transition_{prev_state.value}_to_{self.state.value}"
            )
        
        return result if result is not None else self.current_scene
    
    def _handle_idle(
        self, chunk_id: int, target_scene: str, confidence: float, book_id: str
    ) -> Optional[str]:
        """Handle IDLE state."""
        # If k_consecutive is 0, start playing immediately
        if self.k_consecutive <= 0:
            self.state = ControllerState.PLAYING
            self.current_scene = target_scene
            self.last_switch_time = time.time()
            
            self.logger.log_event(
                chunk_id=chunk_id,
                confidence=confidence,
                target_scene=target_scene,
                active_scene=target_scene,
                reason="started_playing_immediately_k=0"
            )
            self.logger.update_status(
                book_id=book_id,
                chunk_id=chunk_id,
                total_chunks=self.total_chunks,
                scene=target_scene,
                active_bin=target_scene,
                confidence=confidence
            )
            
            return target_scene
        
        # Otherwise, start tracking
        self.state = ControllerState.TRACKING
        self.target_scene = target_scene
        
        self.logger.log_event(
            chunk_id=chunk_id,
            confidence=confidence,
            target_scene=target_scene,
            active_scene=None,
            reason="started_tracking"
        )
        self.logger.update_status(
            book_id=book_id,
            chunk_id=chunk_id,
            total_chunks=self.total_chunks,
            scene=target_scene,
            confidence=confidence
        )
        
        return None
    
    def _handle_tracking(
        self, chunk_id: int, target_scene: str, confidence: float, book_id: str
    ) -> Optional[str]:
        """Handle TRACKING state."""
        # Check if we have K consecutive chunks
        if self.consecutive_count >= self.k_consecutive:
            self.state = ControllerState.PLAYING
            self.current_scene = target_scene
            self.last_switch_time = time.time()
            
            self.logger.log_event(
                chunk_id=chunk_id,
                confidence=confidence,
                target_scene=target_scene,
                active_scene=target_scene,
                reason=f"started_playing_after_{self.k_consecutive}_consecutive"
            )
            self.logger.update_status(
                book_id=book_id,
                chunk_id=chunk_id,
                total_chunks=self.total_chunks,
                scene=target_scene,
                active_bin=target_scene,
                confidence=confidence
            )
            
            return target_scene
        
        # Still tracking
        self.logger.update_status(
            book_id=book_id,
            chunk_id=chunk_id,
            total_chunks=self.total_chunks,
            scene=target_scene,
            confidence=confidence
        )
        
        return None
    
    def _handle_playing(
        self, chunk_id: int, target_scene: str, confidence: float, book_id: str, current_time: float
    ) -> Optional[str]:
        """Handle PLAYING state."""
        # Calculate dwell remaining
        elapsed = current_time - self.last_switch_time
        dwell_remaining = max(0, self.dwell_time_sec - elapsed)
        
        self.logger.update_status(
            book_id=book_id,
            chunk_id=chunk_id,
            total_chunks=self.total_chunks,
            scene=target_scene,
            active_bin=self.current_scene,
            dwell_remaining=dwell_remaining,
            confidence=confidence
        )
        
        # Check if scene changed
        if target_scene != self.current_scene:
            # Check if dwell time satisfied
            if dwell_remaining <= 0:
                # If k_consecutive <= 0, switch immediately
                if self.k_consecutive <= 0:
                    old_scene = self.current_scene
                    self.current_scene = target_scene
                    self.last_switch_time = current_time
                    
                    self.logger.log_event(
                        chunk_id=chunk_id,
                        confidence=confidence,
                        target_scene=target_scene,
                        active_scene=target_scene,
                        reason=f"switched_from_{old_scene}_immediately_k=0"
                    )
                    self.logger.update_status(
                        book_id=book_id,
                        chunk_id=chunk_id,
                        total_chunks=self.total_chunks,
                        scene=target_scene,
                        active_bin=target_scene,
                        dwell_remaining=self.dwell_time_sec,
                        confidence=confidence
                    )
                    
                    return target_scene
                else:
                    # Need k_consecutive confirmations, enter SWITCH_PENDING
                    self.state = ControllerState.SWITCH_PENDING
                    self.target_scene = target_scene
                    self.consecutive_count = 1
                    
                    self.logger.log_event(
                        chunk_id=chunk_id,
                        confidence=confidence,
                        target_scene=target_scene,
                        active_scene=self.current_scene,
                        reason="switch_pending_dwell_satisfied"
                    )
            else:
                self.logger.log_event(
                    chunk_id=chunk_id,
                    confidence=confidence,
                    target_scene=target_scene,
                    active_scene=self.current_scene,
                    reason=f"switch_blocked_dwell_remaining_{dwell_remaining:.0f}s"
                )
        
        return self.current_scene
    
    def _handle_switch_pending(
        self, chunk_id: int, target_scene: str, confidence: float, book_id: str, current_time: float
    ) -> Optional[str]:
        """Handle SWITCH_PENDING state."""
        # Check if target scene is stable
        if target_scene != self.target_scene:
            # Target changed, reset or go back to playing
            self.state = ControllerState.PLAYING
            self.consecutive_count = 1
            
            self.logger.log_event(
                chunk_id=chunk_id,
                confidence=confidence,
                target_scene=target_scene,
                active_scene=self.current_scene,
                reason="switch_cancelled_target_changed"
            )
            
            return self.current_scene
        
        # Check if we have K consecutive chunks for new scene
        if self.consecutive_count >= self.k_consecutive:
            # Switch!
            old_scene = self.current_scene
            self.current_scene = target_scene
            self.state = ControllerState.PLAYING
            self.last_switch_time = current_time
            
            self.logger.log_event(
                chunk_id=chunk_id,
                confidence=confidence,
                target_scene=target_scene,
                active_scene=target_scene,
                reason=f"switched_from_{old_scene}"
            )
            self.logger.update_status(
                book_id=book_id,
                chunk_id=chunk_id,
                total_chunks=self.total_chunks,
                scene=target_scene,
                active_bin=target_scene,
                dwell_remaining=self.dwell_time_sec,
                confidence=confidence
            )
            
            return target_scene
        
        # Still pending
        self.logger.update_status(
            book_id=book_id,
            chunk_id=chunk_id,
            total_chunks=self.total_chunks,
            scene=target_scene,
            active_bin=self.current_scene,
            dwell_remaining=0,
            confidence=confidence
        )
        
        return self.current_scene
    
    def _get_scene_for_chunk(self, chunk_id: int) -> str:
        """
        Get scene for chunk from timeline data.
        Falls back to cycling if no timeline or no scene_label.
        """
        if self.timeline and 'chunks' in self.timeline:
            chunks = self.timeline['chunks']
            # Find chunk by chunk_id
            for chunk in chunks:
                if chunk.get('chunk_id') == chunk_id:
                    scene_label = chunk.get('scene_label')
                    if scene_label:
                        return scene_label
                    break
        
        # Fallback: cycle through scenes (dummy mode)
        return self.scene_list[chunk_id % len(self.scene_list)]
    
    def get_state(self) -> ControllerState:
        """Get current state."""
        return self.state
    
    def get_current_scene(self) -> Optional[str]:
        """Get currently playing scene."""
        return self.current_scene
