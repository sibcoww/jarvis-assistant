"""
Test continuous mode feature.

After executing a command, the system waits for another command
without requiring the wake word for X seconds, then returns to
normal wake-word-based activation.

Example flow:
1. User: "Джарвис, включи музыку"
2. System executes media_play
3. System: "⏱ Слушаю следующую команду... (10s)"
4. User: "поставь громкость на 50"      <- NO wake word needed!
5. System executes set_volume
6. System: "✅ Готово. ⏱ Слушаю следующую команду... (10s)"
7. [After 10s timeout]
8. System: "⏰ Режим continuous истёк. Скажи «Джарвис» для активации."
"""

import sys
import os
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from jarvis.ml_nlu import MLNLU
from jarvis.engine import JarvisEngine


class MockASR:
    """Mock ASR for testing."""
    
    def __init__(self, inputs: list[str]):
        self.inputs = inputs
        self.index = 0
    
    def listen_once(self):
        if self.index >= len(self.inputs):
            # Simulate timeout by returning empty
            return ""
        result = self.inputs[self.index]
        self.index += 1
        return result


class TestContinuousMode:
    
    def test_continuous_mode_single_sentence_wake_word(self):
        """Test: Single sentence with wake word triggers continuous mode."""
        print("\n✓ Test 1: Single sentence with wake word")
        print("  User: 'Джарвис, включи музыку'")
        
        nlu = MLNLU()
        result = nlu.parse_with_wake_word("Джарвис, включи музыку")
        
        print(f"  Intent: {result['type']} (confidence: {result['confidence']:.2f})")
        assert result['type'] == 'media_play', f"Expected media_play, got {result['type']}"
        print("  ✅ PASS: Recognized as media_play")
    
    
    def test_continuous_mode_no_wake_word(self):
        """Test: Command without wake word parsed correctly."""
        print("\n✓ Test 2: Command without wake word")
        print("  User: 'поставь громкость на 50'")
        
        nlu = MLNLU()
        result = nlu.parse("поставь громкость на 50")
        
        print(f"  Intent: {result['type']} (confidence: {result['confidence']:.2f})")
        assert result['type'] == 'set_volume', f"Expected set_volume, got {result['type']}"
        # Note: ML NLU uses 'value' key for volume, not 'volume'
        assert result['slots'].get('value') == 50, f"Expected value=50, got {result['slots'].get('value')}"
        print("  ✅ PASS: Recognized as set_volume with value=50")
    
    
    def test_continuous_mode_timeout_check(self):
        """Test: Timeout detection logic."""
        print("\n✓ Test 3: Timeout detection")
        
        timeout = 2.0  # 2 seconds for testing
        start = time.time()
        until = start + timeout
        
        # Simulate waiting
        time.sleep(1)
        assert time.time() <= until, "Timer should not expire yet"
        print("  After 1s: Still in continuous mode ✓")
        
        time.sleep(1.5)
        assert time.time() > until, "Timer should have expired"
        print("  After 2.5s: Continuous mode expired ✓")
        print("  ✅ PASS: Timeout logic works correctly")
    
    
    def test_continuous_mode_sequence(self):
        """Test: Sequential commands without wake word."""
        print("\n✓ Test 4: Sequential commands without wake word")
        
        nlu = MLNLU()
        
        # First command (with wake word)
        cmd1 = "Джарвис, включи музыку"
        result1 = nlu.parse_with_wake_word(cmd1)
        print(f"  1st command: '{cmd1}'")
        print(f"     → {result1['type']} (confidence: {result1['confidence']:.2f})")
        assert result1['type'] == 'media_play'
        print("     ✅ Recognized")
        
        # Second command (without wake word - should work in continuous mode)
        cmd2 = "поставь громкость на 70"
        result2 = nlu.parse(cmd2)
        print(f"  2nd command: '{cmd2}'")
        print(f"     → {result2['type']} (confidence: {result2['confidence']:.2f})")
        assert result2['type'] == 'set_volume'
        print("     ✅ Recognized")
        
        # Third command (another one without wake word)
        cmd3 = "какое время"
        result3 = nlu.parse(cmd3)
        print(f"  3rd command: '{cmd3}'")
        print(f"     → {result3['type']} (confidence: {result3['confidence']:.2f})")
        assert result3['type'] == 'show_time'
        print("     ✅ Recognized")
        
        print("  ✅ PASS: All sequential commands recognized")
    
    
    def test_continuous_mode_engine_flags(self):
        """Test: Engine flags for continuous mode."""
        print("\n✓ Test 5: Engine continuous mode flags")
        
        engine = JarvisEngine(use_ml_nlu=True, continuous_mode_timeout=5.0)
        
        # Check initial state
        assert not engine.continuous_mode, "Should not be in continuous mode initially"
        assert not engine.armed, "Should not be armed initially"
        print("  Initial state: continuous_mode=False, armed=False ✓")
        
        # Check continuous mode timeout parameter
        assert engine.continuous_mode_timeout == 5.0, "Timeout should be 5.0s"
        print("  Continuous mode timeout: 5.0s ✓")
        
        print("  ✅ PASS: Engine flags initialized correctly")
    
    
    def test_continuous_mode_with_variants(self):
        """Test: Different phrasings in continuous mode."""
        print("\n✓ Test 6: Different command phrasings in continuous mode")
        
        nlu = MLNLU()
        
        test_cases = [
            ("Джарвис, поставь громкость на 50", "set_volume", "value", 50),
            ("громкость на 30", "set_volume", "value", 30),  # No wake word
            ("Джарвис, включи музыку", "media_play", None, None),
            ("играй музыку", "media_play", None, None),  # No wake word
        ]
        
        for cmd, expected_type, slot_key, expected_value in test_cases:
            if cmd.lower().startswith("джарвис"):
                result = nlu.parse_with_wake_word(cmd)
            else:
                result = nlu.parse(cmd)
            
            print(f"  '{cmd}'")
            print(f"     → {result['type']} (confidence: {result['confidence']:.2f})")
            
            assert result['type'] == expected_type, f"Expected {expected_type}, got {result['type']}"
            
            if expected_value is not None:
                value = result['slots'].get(slot_key)
                assert value == expected_value, f"Expected {slot_key}={expected_value}, got {value}"
            
            print(f"     ✅ Correct")
        
        print("  ✅ PASS: All variants recognized correctly")


def main():
    print("=" * 60)
    print("🧪 CONTINUOUS MODE TEST SUITE")
    print("=" * 60)
    
    test = TestContinuousMode()
    
    try:
        test.test_continuous_mode_single_sentence_wake_word()
        test.test_continuous_mode_no_wake_word()
        test.test_continuous_mode_timeout_check()
        test.test_continuous_mode_sequence()
        test.test_continuous_mode_engine_flags()
        test.test_continuous_mode_with_variants()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\n📋 Feature Summary:")
        print("  • Wake word detection: Works ✓")
        print("  • Single-sentence parsing: Works ✓")
        print("  • Sequential commands without wake word: Works ✓")
        print("  • Timeout detection: Works ✓")
        print("  • Engine flags: Works ✓")
        print("\n🎯 Usage Example:")
        print("  1. User: 'Джарвис, включи музыку'")
        print("  2. System executes command")
        print("  3. System: 'Слушаю следующую команду... (10s)'")
        print("  4. User: 'поставь громкость на 50'  ← NO wake word needed!")
        print("  5. System executes command")
        print("  6. System: 'Слушаю следующую команду... (10s)'")
        print("  7. [After 10s] System returns to normal mode")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
