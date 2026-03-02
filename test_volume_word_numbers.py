"""
Test volume extraction with word numbers (двадцать, сто, девяносто).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from jarvis.ml_nlu import MLNLU


def test_volume_word_numbers():
    """Test that volume commands with word numbers work correctly."""
    
    print("=" * 70)
    print("🧪 TESTING VOLUME EXTRACTION WITH WORD NUMBERS")
    print("=" * 70)
    
    nlu = MLNLU()
    
    test_cases = [
        ("поставь громкость на двадцать", "set_volume", 20),
        ("громкость на сто", "set_volume", 100),
        ("громкость на девяносто", "set_volume", 90),
        ("громкость на пятьдесят", "set_volume", 50),
        ("поставь громкость на тридцать", "set_volume", 30),
        ("громкость на восемьдесят", "set_volume", 80),
        ("поставь громкость на 50", "set_volume", 50),  # Digits still work
        ("громкость 75", "set_volume", 75),
    ]
    
    print("\n📝 Test Cases:\n")
    
    passed = 0
    failed = 0
    
    for text, expected_intent, expected_value in test_cases:
        result = nlu.parse(text)
        
        intent_ok = result["type"] == expected_intent
        value_ok = result["slots"].get("value") == expected_value
        
        status = "✅" if (intent_ok and value_ok) else "❌"
        
        print(f"{status} '{text}'")
        print(f"   Expected: {expected_intent} (value={expected_value})")
        print(f"   Got:      {result['type']} (value={result['slots'].get('value')}) "
              f"[conf: {result['confidence']:.2f}]")
        
        if intent_ok and value_ok:
            passed += 1
        else:
            failed += 1
            if not intent_ok:
                print(f"   ⚠️  Wrong intent!")
            if not value_ok:
                print(f"   ⚠️  Wrong value!")
        
        print()
    
    print("=" * 70)
    print(f"RESULTS: {passed}/{len(test_cases)} passed")
    
    if failed == 0:
        print("✅ ALL TESTS PASSED!")
    else:
        print(f"❌ {failed} tests failed")
    
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = test_volume_word_numbers()
    exit(0 if success else 1)
