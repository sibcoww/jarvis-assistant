"""
Porcupine-based wake word detection for 'Джарвис' keyword.
Runs in background thread for non-blocking detection.
"""

import logging
import threading
from typing import Optional, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import pvporcupine
try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logger.warning("pvporcupine not installed. Using simple keyword matching fallback.")


class PorcupineWakeWord:
    """Porcupine-based wake word detector for 'Джарвис'.
    
    Runs in background thread and triggers callback when wake word is detected.
    """
    
    def __init__(
        self,
        keyword: str = "джарвис",
        access_key: Optional[str] = None,
        sensitivity: float = 0.5,
        on_detected: Optional[Callable] = None
    ):
        """Initialize Porcupine wake word detector.
        
        Args:
            keyword: Wake word keyword (default: 'джарвис')
            access_key: Porcupine access key (if not set, falls back to simple matching)
            sensitivity: Detection sensitivity (0.0-1.0, default 0.5)
            on_detected: Callback function to call when wake word is detected
        """
        self.keyword = keyword
        self.sensitivity = max(0.0, min(1.0, sensitivity))  # Clamp to 0-1
        self.on_detected = on_detected or self._default_callback
        self.access_key = access_key
        
        self.is_listening = False
        self.detector = None
        self.detector_thread = None
        
        # Try to initialize Porcupine
        if PORCUPINE_AVAILABLE and access_key:
            try:
                self._init_porcupine()
            except Exception as e:
                logger.warning(f"Failed to initialize Porcupine: {e}. Using fallback.")
                self.detector = None
        else:
            logger.info("Using simple keyword matching fallback (no Porcupine access key)")
            self.detector = None
    
    def _init_porcupine(self):
        """Initialize Porcupine detector with Russian model."""
        logger.info("Initializing Porcupine wake word detector...")
        
        # Create detector for 'джарвис' keyword in Russian
        # Porcupine has built-in keywords for multiple languages
        try:
            self.detector = pvporcupine.create(
                access_key=self.access_key,
                keywords=["jarvis"],  # Porcupine recognizes 'jarvis' in any language
                sensitivities=[self.sensitivity]
            )
            logger.info(f"Porcupine initialized: {self.detector.sample_rate}Hz, "
                       f"{self.detector.frame_length} frames/sample")
        except Exception as e:
            logger.error(f"Porcupine initialization failed: {e}")
            self.detector = None
    
    def _default_callback(self, keyword: str):
        """Default callback when wake word is detected."""
        logger.info(f"🔊 Wake word detected: {keyword}")
    
    def heard(self, text: str) -> bool:
        """Simple keyword matching (for testing/fallback).
        
        Args:
            text: Input text to check
            
        Returns:
            True if keyword is detected in text
        """
        return self.keyword in text.lower()
    
    def start_listening(self):
        """Start background listening thread for wake word detection."""
        if self.is_listening:
            logger.warning("Already listening")
            return
        
        if not self.detector:
            logger.warning("Porcupine detector not initialized. Fallback mode only.")
            return
        
        self.is_listening = True
        self.detector_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="PorcupineListener"
        )
        self.detector_thread.start()
        logger.info("🎤 Wake word listener started in background thread")
    
    def stop_listening(self):
        """Stop background listening thread."""
        if not self.is_listening:
            return
        
        self.is_listening = False
        if self.detector_thread and self.detector_thread.is_alive():
            self.detector_thread.join(timeout=2.0)
            logger.info("🎤 Wake word listener stopped")
    
    def _listen_loop(self):
        """Background listening loop (runs in separate thread).
        
        This loop captures audio and processes it with Porcupine detector.
        For a complete implementation, you'd integrate with:
        - sounddevice.InputStream for audio capture
        - Porcupine's process() method for each audio frame
        """
        logger.info("Starting Porcupine listen loop...")
        
        if not self.detector:
            logger.error("Detector not initialized")
            return
        
        try:
            import sounddevice as sd
            import numpy as np
            
            # Start audio stream
            with sd.InputStream(
                samplerate=self.detector.sample_rate,
                channels=1,
                blocksize=self.detector.frame_length,
                dtype=np.int16
            ) as stream:
                logger.info("Audio stream started")
                
                while self.is_listening:
                    try:
                        # Read audio frame
                        audio_frame, _ = stream.read(self.detector.frame_length)
                        audio_frame = audio_frame.flatten().astype(np.int16)
                        
                        # Process with Porcupine
                        keyword_index = self.detector.process(audio_frame)
                        
                        if keyword_index >= 0:
                            logger.info(f"✅ Wake word detected! (index: {keyword_index})")
                            self.on_detected(self.keyword)
                    
                    except Exception as e:
                        logger.error(f"Error in listen loop: {e}")
                        break
        
        except ImportError:
            logger.error("sounddevice not installed. Cannot run listen loop.")
        except Exception as e:
            logger.error(f"Unexpected error in listen loop: {e}")
        finally:
            logger.info("Listen loop ended")
    
    def __enter__(self):
        """Context manager entry."""
        self.start_listening()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_listening()
        if self.detector:
            self.detector.delete()
    
    def __del__(self):
        """Cleanup on destruction."""
        self.stop_listening()
        if self.detector:
            try:
                self.detector.delete()
            except:
                pass


class SimpleWakeWord:
    """Simple wake word detector using keyword matching (fallback)."""
    
    def __init__(self, keyword: str = "джарвис"):
        """Initialize simple wake word detector.
        
        Args:
            keyword: Wake word keyword
        """
        self.keyword = keyword
    
    def heard(self, text: str) -> bool:
        """Check if keyword is in text.
        
        Args:
            text: Input text
            
        Returns:
            True if keyword detected
        """
        return self.keyword in text.lower()
    
    def start_listening(self):
        """No-op for simple detector."""
        pass
    
    def stop_listening(self):
        """No-op for simple detector."""
        pass


# Factory function to get appropriate detector
def get_wakeword_detector(
    use_porcupine: bool = False,
    access_key: Optional[str] = None,
    sensitivity: float = 0.5,
    on_detected: Optional[Callable] = None
):
    """Get wake word detector (Porcupine or simple fallback).
    
    Args:
        use_porcupine: If True, try to use Porcupine
        access_key: Porcupine access key
        sensitivity: Detection sensitivity
        on_detected: Callback when wake word detected
        
    Returns:
        Wake word detector instance
    """
    if use_porcupine and PORCUPINE_AVAILABLE and access_key:
        logger.info("Using Porcupine wake word detector")
        return PorcupineWakeWord(
            access_key=access_key,
            sensitivity=sensitivity,
            on_detected=on_detected
        )
    else:
        logger.info("Using simple keyword matching wake word detector")
        return SimpleWakeWord()


# Alias for backward compatibility
WakeWord = SimpleWakeWord
