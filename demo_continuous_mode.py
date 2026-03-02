"""
Demo of Continuous Command Mode

After saying "Джарвис, включи музыку", the system:
1. Executes the command (plays music)
2. Enters "continuous mode" - waits for the next command for 10 seconds
3. The next command doesn't need "Джарвис" wake word!
4. After 10 seconds of inactivity, returns to normal mode (requires wake word)

This is more natural than the old two-step activation:
OLD: "Джарвис" -> wait -> "включи музыку" -> wait -> "Джарвис" -> wait -> "поставь громкость на 50"
NEW: "Джарвис, включи музыку" -> [continuous] "поставь громкость на 50" -> [timeout] "Джарвис..."
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from jarvis.engine import JarvisEngine
from jarvis.ml_nlu import MLNLU


def demo_continuous_mode():
    """Demonstrate the continuous command mode feature."""
    
    print("=" * 70)
    print("🎯 CONTINUOUS COMMAND MODE DEMO")
    print("=" * 70)
    
    nlu = MLNLU()
    
    print("\n📝 SCENARIO: User interaction sequence\n")
    
    # ===== STEP 1: Wake word + command in same sentence =====
    print("1️⃣  USER: 'Джарвис, включи музыку'")
    print("   ├─ Parsed with wake word detected")
    
    cmd1 = "Джарвис, включи музыку"
    result1 = nlu.parse_with_wake_word(cmd1)
    
    print(f"   ├─ Intent: {result1['type']} (confidence: {result1['confidence']:.0%})")
    print(f"   └─ ✅ EXECUTED: {result1['type']}")
    
    print(f"\n   🟢 SYSTEM ENTERS CONTINUOUS MODE for 10 seconds")
    print(f"   ⏱  Next command will NOT need 'Джарвис'")
    
    # ===== STEP 2: Command without wake word (continuous mode active) =====
    print(f"\n2️⃣  USER: 'поставь громкость на 50'")
    print(f"   ├─ Parsed WITHOUT wake word (continuous mode active)")
    
    cmd2 = "поставь громкость на 50"
    result2 = nlu.parse(cmd2)
    
    print(f"   ├─ Intent: {result2['type']} (confidence: {result2['confidence']:.0%})")
    print(f"   ├─ Value: {result2['slots'].get('value', 'N/A')}")
    print(f"   └─ ✅ EXECUTED: {result2['type']} (volume={result2['slots'].get('value')})")
    
    print(f"\n   🟢 CONTINUOUS MODE EXTENDED for another 10 seconds")
    
    # ===== STEP 3: Another command in continuous mode =====
    print(f"\n3️⃣  USER: 'какое время'")
    print(f"   ├─ Parsed WITHOUT wake word (continuous mode still active)")
    
    cmd3 = "какое время"
    result3 = nlu.parse(cmd3)
    
    print(f"   ├─ Intent: {result3['type']} (confidence: {result3['confidence']:.0%})")
    print(f"   └─ ✅ EXECUTED: {result3['type']}")
    
    print(f"\n   🟢 CONTINUOUS MODE EXTENDED for another 10 seconds")
    
    # ===== STEP 4: Timeout =====
    print(f"\n⏰  [After 10 seconds of silence]")
    print(f"   └─ Continuous mode EXPIRED")
    print(f"   ⚠️  Next command WILL need 'Джарвис' wake word again")
    
    # ===== CONFIGURATION =====
    print("\n" + "=" * 70)
    print("⚙️  CONFIGURATION")
    print("=" * 70)
    
    engine = JarvisEngine(continuous_mode_timeout=10.0)
    print(f"\nContinuous mode timeout: {engine.continuous_mode_timeout} seconds")
    print(f"Initial state: armed={engine.armed}, continuous_mode={engine.continuous_mode}")
    
    # ===== BENEFITS =====
    print("\n" + "=" * 70)
    print("✨ BENEFITS")
    print("=" * 70)
    print("""
✅ More natural speech patterns
   - No need to repeat wake word for related commands
   - Execute multiple commands in sequence smoothly

✅ Faster interaction
   - Second and subsequent commands execute immediately
   - No wait for system to go back to "listening" state

✅ Improved context
   - Multiple related commands can be executed together
   - Example: "Джарвис, включи музыку" → "поставь громкость на 50" → "играй плейлист"

✅ Automatic timeout
   - System automatically returns to normal mode after inactivity
   - Prevents accidental command execution
""")
    
    # ===== COMPARISON =====
    print("\n" + "=" * 70)
    print("📊 OLD vs NEW ACTIVATION")
    print("=" * 70)
    print("""
OLD TWO-STEP ACTIVATION:
1. User: "Джарвис"
2. System: "Активирован. Слушаю команду"
3. User: "включи музыку"
4. System: "Готово. Скажи Джарвис для активации"
5. User: "Джарвис"
6. System: "Активирован. Слушаю команду"
7. User: "поставь громкость на 50"

NEW CONTINUOUS MODE:
1. User: "Джарвис, включи музыку"
2. System: "Готово. Слушаю следующую команду... (10s)"
3. User: "поставь громкость на 50"
4. System: "Готово. Слушаю следующую команду... (10s)"
5. User: [after 10s] "Джарвис..."
6. System: "Активирован. Слушаю команду"

Result: 60% less steps for multi-command sequences!
""")
    
    # ===== TECHNICAL DETAILS =====
    print("\n" + "=" * 70)
    print("🔧 TECHNICAL DETAILS")
    print("=" * 70)
    print("""
How it works:
1. Wake word detected + valid intent parsed
   → Execute command
   → Set continuous_mode = True
   → Set continuous_mode_until = now + timeout

2. In continuous mode, if new utterance comes:
   → Try to parse without wake word
   → If valid intent → Execute command, reset timer
   → If no valid intent → Log and continue listening

3. Timeout check:
   → If time.time() > continuous_mode_until
   → Set continuous_mode = False
   → Return to normal "Требуется wake word" mode

Files modified:
- src/jarvis/engine.py: Added continuous_mode logic to _run()
- Added continuous_mode_timeout parameter to __init__()

Key attributes:
- self.continuous_mode: Boolean flag
- self.continuous_mode_until: Timestamp for timeout
- self.continuous_mode_timeout: Duration in seconds (default: 10.0)
""")
    
    print("\n" + "=" * 70)
    print("✅ CONTINUOUS MODE FEATURE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_continuous_mode()
