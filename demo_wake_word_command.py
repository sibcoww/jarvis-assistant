#!/usr/bin/env python3
"""
Demo: Wake Word + Command in Single Sentence

Shows the new feature where you can say "Джарвис, поставь громкость на 50"
instead of first saying "Джарвис" and then "поставь громкость на 50"
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


def main():
    print("\n" + "=" * 70)
    print("Demo: Wake Word + Command in Single Sentence")
    print("=" * 70)
    
    nlu = MLNLU()
    
    print("\nBefore (two steps):")
    print("  1. User: 'Джарвис'")
    print("  2. System: 'Активирован, слушаю команду'")
    print("  3. User: 'Поставь громкость на 50'")
    print("  4. System: 'Готово'")
    
    print("\nAfter (one step - NEW):")
    print("  1. User: 'Джарвис, поставь громкость на 50'")
    print("  2. System: 'Готово'")
    
    print("\n" + "=" * 70)
    print("Supported Formats:")
    print("=" * 70)
    
    examples = [
        ("джарвис поставь громкость на 50", "Direct: wake word + space + command"),
        ("джарвис, поставь громкость на 50", "With comma: wake word + comma + command"),
        ("джарвис включи музыку", "Simple command"),
        ("джарвис какое время", "Query"),
        ("джарвис открой браузер", "App launch"),
    ]
    
    print("\nTesting various formats:\n")
    
    for text, description in examples:
        result = nlu.parse_with_wake_word(text)
        
        print(f"Input:  '{text}'")
        print(f"Format: {description}")
        print(f"Result: {result['type']}")
        if result['slots']:
            print(f"Params: {result['slots']}")
        print(f"Confidence: {result['confidence']:.2f}")
        print()
    
    print("=" * 70)
    print("Technical Details:")
    print("=" * 70)
    
    print("""
1. Wake Word Detection:
   - Automatically detects 'джарвис' keyword
   - Also recognizes variations: жарвис, джервис, джанверт
   
2. Wake Word Stripping:
   - Removes wake word and punctuation from input
   - 'джарвис, поставь...' -> 'поставь...'
   - 'джарвис поставь...' -> 'поставь...'
   
3. Intent Recognition:
   - Uses ML-based NLU with spaCy embeddings
   - Recognizes command after wake word is stripped
   - Returns confidence score for validation
   
4. Parameter Extraction:
   - Automatically extracts values (e.g., '50' from 'громкость на 50')
   - Works with or without wake word
   
5. Backward Compatibility:
   - Still supports two-step activation:
     Step 1: 'Джарвис' (arms the system)
     Step 2: 'Включи музыку' (executes command)
   - Both methods work seamlessly
    """)
    
    print("=" * 70)
    print("Use Cases:")
    print("=" * 70)
    
    use_cases = [
        "Джарвис, включи музыку",
        "Джарвис, какое время",
        "Джарвис, поставь громкость на 70",
        "Джарвис, открой браузер",
        "Джарвис, запусти телеграм",
        "Джарвис, покажи дату",
        "Джарвис, сделай музыку тише",
        "Джарвис, далее",
    ]
    
    print()
    for i, case in enumerate(use_cases, 1):
        result = nlu.parse_with_wake_word(case)
        status = "[OK]" if result['type'] != "unknown" else "[?]"
        print(f"{i:2}. {case:<40} -> {result['type']:<15} {status}")
    
    print("\n" + "=" * 70)
    print("Demo Complete!")
    print("=" * 70)
    print("\nYou can now use Jarvis Assistant more naturally!")
    print("Try saying: 'Джарвис, поставь громкость на 50'")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
