"""
Tests for wake word detection system.
"""

import unittest
import time
import threading
from pathlib import Path
import sys
import os

# Fix Unicode encoding on Windows
if os.name == 'nt':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jarvis.wakeword import SimpleWakeWord, PorcupineWakeWord, get_wakeword_detector


class TestSimpleWakeWord(unittest.TestCase):
    """Test simple keyword-based wake word detector."""
    
    def setUp(self):
        """Initialize detector."""
        self.detector = SimpleWakeWord(keyword="джарвис")
    
    def test_exact_keyword_match(self):
        """Test exact keyword detection."""
        self.assertTrue(self.detector.heard("джарвис"))
        print("  ✓ Exact match detected")
    
    def test_keyword_in_sentence(self):
        """Test keyword detection in sentence."""
        self.assertTrue(self.detector.heard("джарвис включи музыку"))
        self.assertTrue(self.detector.heard("привет джарвис"))
        print("  ✓ Keyword in sentence detected")
    
    def test_case_insensitive(self):
        """Test case-insensitive matching."""
        self.assertTrue(self.detector.heard("ДЖАРВИС"))
        self.assertTrue(self.detector.heard("Джарвис"))
        self.assertTrue(self.detector.heard("ДжАрВиС"))
        print("  ✓ Case-insensitive matching works")
    
    def test_keyword_not_present(self):
        """Test rejection of texts without keyword."""
        self.assertFalse(self.detector.heard("включи музыку"))
        self.assertFalse(self.detector.heard("привет"))
        self.assertFalse(self.detector.heard(""))
        print("  ✓ Keyword absence correctly detected")
    
    def test_listening_methods(self):
        """Test start/stop listening methods (no-op for simple detector)."""
        self.detector.start_listening()
        self.assertFalse(self.detector.heard("что-то"))
        self.detector.stop_listening()
        print("  ✓ Listening methods work (no-op)")


class TestPorcupineWakeWord(unittest.TestCase):
    """Test Porcupine-based wake word detector."""
    
    def test_porcupine_instantiation(self):
        """Test Porcupine detector instantiation without access key."""
        # Without access key, should create detector but not initialize Porcupine
        detector = PorcupineWakeWord()
        self.assertIsNotNone(detector)
        print("  ✓ Porcupine detector instantiated")
    
    def test_porcupine_fallback_heard(self):
        """Test fallback keyword matching."""
        detector = PorcupineWakeWord()
        self.assertTrue(detector.heard("джарвис"))
        self.assertTrue(detector.heard("Джарвис включи свет"))
        self.assertFalse(detector.heard("включи свет"))
        print("  ✓ Fallback keyword matching works")
    
    def test_porcupine_callback(self):
        """Test callback functionality."""
        detected = []
        
        def on_detect(keyword):
            detected.append(keyword)
        
        detector = PorcupineWakeWord(on_detected=on_detect)
        # Simulate detection
        if detector.on_detected:
            detector.on_detected("джарвис")
        
        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0], "джарвис")
        print("  ✓ Callback triggered on detection")
    
    def test_porcupine_sensitivity(self):
        """Test sensitivity parameter."""
        # Valid sensitivity values
        for sensitivity in [0.0, 0.25, 0.5, 0.75, 1.0]:
            detector = PorcupineWakeWord(sensitivity=sensitivity)
            self.assertAlmostEqual(detector.sensitivity, sensitivity)
            print(f"  ✓ Sensitivity {sensitivity} accepted")
        
        # Out of range values should be clamped
        detector = PorcupineWakeWord(sensitivity=1.5)
        self.assertAlmostEqual(detector.sensitivity, 1.0)
        detector = PorcupineWakeWord(sensitivity=-0.5)
        self.assertAlmostEqual(detector.sensitivity, 0.0)
        print("  ✓ Out-of-range sensitivity values clamped")
    
    def test_context_manager(self):
        """Test context manager functionality."""
        detected = []
        
        def on_detect(keyword):
            detected.append(keyword)
        
        detector = PorcupineWakeWord(on_detected=on_detect)
        
        with detector as d:
            self.assertEqual(d, detector)
            self.assertFalse(detector.is_listening)  # No actual listening without Porcupine
        
        self.assertFalse(detector.is_listening)
        print("  ✓ Context manager works correctly")


class TestWakeWordFactory(unittest.TestCase):
    """Test wake word detector factory function."""
    
    def test_simple_detector_default(self):
        """Test that simple detector is default."""
        detector = get_wakeword_detector()
        self.assertEqual(type(detector).__name__, "SimpleWakeWord")
        print("  ✓ Default detector is SimpleWakeWord")
    
    def test_simple_detector_explicit(self):
        """Test explicit simple detector selection."""
        detector = get_wakeword_detector(use_porcupine=False)
        self.assertEqual(type(detector).__name__, "SimpleWakeWord")
        print("  ✓ Explicit SimpleWakeWord selection works")
    
    def test_porcupine_without_key(self):
        """Test that Porcupine falls back without access key."""
        detector = get_wakeword_detector(use_porcupine=True)
        # Should fall back to SimpleWakeWord if no access key
        self.assertEqual(type(detector).__name__, "SimpleWakeWord")
        print("  ✓ Porcupine falls back without access key")
    
    def test_detector_with_callback(self):
        """Test detector creation with callback."""
        detected = []
        
        def on_detect(keyword):
            detected.append(keyword)
        
        detector = get_wakeword_detector(on_detected=on_detect)
        detector.on_detected("test")
        
        self.assertEqual(detected[0], "test")
        print("  ✓ Callback passed to detector")


class TestWakeWordIntegration(unittest.TestCase):
    """Integration tests for wake word detection."""
    
    def test_multiple_detectors(self):
        """Test running multiple detectors."""
        detectors = [
            get_wakeword_detector(),
            SimpleWakeWord("джарвис"),
            PorcupineWakeWord(),
        ]
        
        for detector in detectors:
            self.assertTrue(detector.heard("джарвис"))
        
        print(f"  ✓ All {len(detectors)} detectors work")
    
    def test_detector_threading(self):
        """Test that listening doesn't block main thread."""
        detector = SimpleWakeWord()
        
        # Start listening (no-op for simple detector)
        detector.start_listening()
        
        # This should not block
        time.sleep(0.1)
        
        # Should still be responsive
        result = detector.heard("джарвис")
        self.assertTrue(result)
        
        detector.stop_listening()
        print("  ✓ Non-blocking detection works")
    
    def test_keyword_variations(self):
        """Test detection with different keyword variations."""
        detector = SimpleWakeWord(keyword="джарвис")
        
        variations = [
            "джарвис",
            "Джарвис",
            "ДЖАРВИС",
            "джарвис включи",
            "привет джарвис",
            "джарвис скажи",
        ]
        
        for var in variations:
            result = detector.heard(var)
            self.assertTrue(result, f"Failed to detect: {var}")
        
        print(f"  ✓ All {len(variations)} variations detected")


class TestWakeWordPerformance(unittest.TestCase):
    """Performance tests for wake word detection."""
    
    def test_detection_speed(self):
        """Test detection speed."""
        detector = SimpleWakeWord()
        
        iterations = 1000
        start = time.time()
        
        for _ in range(iterations):
            detector.heard("джарвис включи музыку")
        
        elapsed = time.time() - start
        speed = iterations / elapsed
        
        print(f"  ✓ Detection speed: {speed:.0f} checks/sec ({elapsed:.3f}s for {iterations} checks)")
        self.assertGreater(speed, 1000)  # Should be very fast
    
    def test_memory_efficiency(self):
        """Test memory efficiency of multiple detectors."""
        import sys
        
        detector = SimpleWakeWord()
        size = sys.getsizeof(detector)
        
        print(f"  ✓ Detector object size: {size} bytes")
        self.assertLess(size, 1000)  # Should be small


if __name__ == "__main__":
    print("=" * 60)
    print("Wake Word Detection Tests")
    print("=" * 60)
    
    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestSimpleWakeWord))
    suite.addTests(loader.loadTestsFromTestCase(TestPorcupineWakeWord))
    suite.addTests(loader.loadTestsFromTestCase(TestWakeWordFactory))
    suite.addTests(loader.loadTestsFromTestCase(TestWakeWordIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestWakeWordPerformance))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("✅ ALL TESTS PASSED!")
    else:
        print(f"❌ TESTS FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 60)
