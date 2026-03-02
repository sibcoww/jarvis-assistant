#!/usr/bin/env python3
"""
Test wake word + command in single sentence
"""

import sys
import os
from pathlib import Path

# Fix Unicode encoding on Windows
if os.name == 'nt':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from jarvis.ml_nlu import MLNLU


def test_wake_word_command():
    """Test parsing wake word + command in single sentence."""
    
    print("=" * 70)
    print("Testing: Wake Word + Command in Single Sentence")
    print("=" * 70)
    
    nlu = MLNLU()
    
    test_cases = [
        # (input, expected_intent, description)
        ("джарвис поставь громкость на 50", "set_volume", "Wake word + volume command"),
        ("джарвис включи музыку", "media_play", "Wake word + media play"),
        ("джарвис какое время", "show_time", "Wake word + time query"),
        ("джарвис, открой браузер", "open_app", "Wake word with comma + app open"),
        ("привет джарвис включи свет", "unknown", "Wake word in middle with command"),
        ("включи музыку", "media_play", "Command without wake word"),
        ("джарвис", "unknown", "Wake word only (no command)"),
    ]
    
    print("\nTest Results:")
    print("-" * 70)
    print(f"{'Input':<40} | {'Intent':<15} | {'Result'}")
    print("-" * 70)
    
    passed = 0
    failed = 0
    
    for input_text, expected_intent, description in test_cases:
        result = nlu.parse_with_wake_word(input_text)
        actual_intent = result["type"]
        confidence = result["confidence"]
        wake_word_detected = result["wake_word_detected"]
        
        # Check if test passed
        if expected_intent == "unknown":
            test_passed = actual_intent == expected_intent or confidence < 0.5
        else:
            test_passed = actual_intent == expected_intent and confidence > 0.3
        
        status = "[PASS]" if test_passed else "[FAIL]"
        if test_passed:
            passed += 1
        else:
            failed += 1
        
        print(f"{input_text:<40} | {actual_intent:<15} | {status}")
        if wake_word_detected:
            print(f"{'':40} | {'':15} | Wake word: YES")
        if confidence < 1.0:
            print(f"{'':40} | {'':15} | Confidence: {confidence:.2f}")
    
    print("-" * 70)
    print(f"\nResults: {passed} passed, {failed} failed")
    
    print("\n" + "=" * 70)
    print("Feature Demonstration:")
    print("=" * 70)
    
    demo_inputs = [
        "джарвис поставь громкость на 50",
        "джарвис включи музыку",
        "джарвис какое время",
        "джарвис запусти браузер",
    ]
    
    print("\nDetailed parsing:")
    print("-" * 70)
    
    for input_text in demo_inputs:
        result = nlu.parse_with_wake_word(input_text)
        
        print(f"\nInput: \"{input_text}\"")
        print(f"  Intent: {result['type']}")
        print(f"  Confidence: {result['confidence']:.2f}")
        print(f"  Wake word detected: {result['wake_word_detected']}")
        
        if result['slots']:
            print(f"  Parameters:")
            for key, value in result['slots'].items():
                print(f"    - {key}: {value}")
    
    print("\n" + "=" * 70)
    if failed == 0:
        print("[OK] All tests passed!")
    else:
        print(f"[WARNING] {failed} tests failed")
    print("=" * 70)


if __name__ == "__main__":
    try:
        test_wake_word_command()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
