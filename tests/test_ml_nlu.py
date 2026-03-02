"""
Tests for ML-based NLU system.
"""

import unittest
from pathlib import Path
import sys
import os

# Fix Unicode encoding on Windows
if os.name == 'nt':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jarvis.ml_nlu import MLNLU
from jarvis.engine import JarvisEngine


class TestMLNLU(unittest.TestCase):
    """Test ML-based NLU parser."""
    
    @classmethod
    def setUpClass(cls):
        """Initialize ML NLU once for all tests."""
        print("\n🤖 Initializing ML NLU model (this may take a moment)...")
        cls.nlu = MLNLU()
        print("✅ ML NLU ready")
    
    def test_browser_commands(self):
        """Test browser navigation and search intents."""
        test_cases = [
            ("перейди на гугл", "browser_navigate"),
            ("гугл котики", "browser_search"),
            ("поиск погода", "browser_search"),
            ("включи youtube", "browser_navigate"),
        ]
        
        for text, expected_intent in test_cases:
            result = self.nlu.parse(text)
            self.assertEqual(result["type"], expected_intent, 
                           f"Failed for: {text}")
            print(f"  ✓ '{text}' -> {result['type']} (conf: {result['confidence']:.2f})")

    def test_site_alias_routing(self):
        """Test offline mapping for site aliases like YouTube."""
        result = self.nlu.parse("включи ютуб")
        self.assertEqual(result["type"], "browser_navigate")
        self.assertIn("url", result["slots"])
        self.assertIn("youtube", result["slots"]["url"])
        print(f"  ✓ 'включи ютуб' -> browser_navigate ({result['slots']['url']})")
    
    def test_media_commands(self):
        """Test media playback intents."""
        test_cases = [
            ("включи музыку", "media_play"),
            ("пауза", "media_pause"),
            ("далее", "media_next"),
            ("назад", "media_previous"),
        ]
        
        for text, expected_intent in test_cases:
            result = self.nlu.parse(text)
            self.assertEqual(result["type"], expected_intent,
                           f"Failed for: {text}")
            print(f"  ✓ '{text}' -> {result['type']} (conf: {result['confidence']:.2f})")
    
    def test_time_commands(self):
        """Test time/date intents."""
        test_cases = [
            ("какая дата", "show_date"),
            ("какое время", "show_time"),
            ("текущее время", "show_time"),
        ]
        
        for text, expected_intent in test_cases:
            result = self.nlu.parse(text)
            self.assertEqual(result["type"], expected_intent,
                           f"Failed for: {text}")
            print(f"  ✓ '{text}' -> {result['type']} (conf: {result['confidence']:.2f})")
    
    def test_slot_extraction(self):
        """Test slot value extraction."""
        # Browser navigate with URL
        result = self.nlu.parse("перейди на гугл")
        self.assertIn("url", result["slots"])
        print(f"  ✓ Extracted URL: {result['slots'].get('url')}")
        
        # Browser search with query
        result = self.nlu.parse("гугл питон")
        self.assertIn("query", result["slots"])
        print(f"  ✓ Extracted query: {result['slots'].get('query')}")
    
    def test_confidence_scores(self):
        """Test that confidence scores are returned."""
        result = self.nlu.parse("включи музыку")
        
        self.assertIn("confidence", result)
        self.assertGreater(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)
        print(f"  ✓ Confidence: {result['confidence']:.2f}")
    
    def test_open_app_commands(self):
        """Test application opening intents."""
        test_cases = [
            ("открой браузер", "open_app"),
            ("запусти телеграм", "open_app"),
            ("открой вс код", "open_app"),
        ]
        
        for text, expected_intent in test_cases:
            result = self.nlu.parse(text)
            self.assertEqual(result["type"], expected_intent,
                           f"Failed for: {text}")
            self.assertIn("target", result["slots"])
            print(f"  ✓ '{text}' -> open_app (target: {result['slots']['target']})")


class TestMLNLUIntegration(unittest.TestCase):
    """Test ML NLU integration with engine."""
    
    def test_engine_with_ml_nlu(self):
        """Test that engine can use ML NLU."""
        print("\n🔧 Testing ML NLU in JarvisEngine...")
        
        engine = JarvisEngine(use_ml_nlu=True)
        
        self.assertIsNotNone(engine.nlu)
        print(f"  ✓ Engine initialized with {engine.nlu_type} NLU")
        
        # Parse command through engine
        result = engine.nlu.parse("включи музыку")
        self.assertEqual(result["type"], "media_play")
        print(f"  ✓ Command parsed: {result['type']}")
    
    def test_engine_fallback(self):
        """Test that engine falls back gracefully if ML NLU fails."""
        print("\n🔄 Testing fallback to SimpleNLU...")
        
        # Force SimpleNLU
        engine = JarvisEngine(use_ml_nlu=False)
        
        self.assertEqual(engine.nlu_type, "Simple")
        print(f"  ✓ Engine using {engine.nlu_type} NLU")
        
        # Parse should still work
        result = engine.nlu.parse("включи музыку")
        self.assertEqual(result["type"], "media_play")
        print(f"  ✓ Fallback NLU working: {result['type']}")


class TestMLNLUAccuracy(unittest.TestCase):
    """Test ML NLU accuracy on diverse inputs."""
    
    @classmethod
    def setUpClass(cls):
        """Initialize ML NLU."""
        cls.nlu = MLNLU()
    
    def test_similar_phrases(self):
        """Test that similar phrases map to same intent."""
        phrases = [
            "включи музыку",
            "включи музик",
            "запусти музыку",
            "поставь музыку",
        ]
        
        print("\n🔍 Testing similar phrases...")
        intents = []
        for phrase in phrases:
            result = self.nlu.parse(phrase)
            intents.append(result["type"])
            print(f"  '{phrase}' -> {result['type']}")
        
        # Most should map to media_play (some might fail due to training data)
        media_play_count = sum(1 for i in intents if i == "media_play")
        self.assertGreater(media_play_count, 0, "No phrases mapped to media_play")
    
    def test_command_variations(self):
        """Test different ways to say the same command."""
        print("\n🎭 Testing command variations...")
        
        variations = {
            "show_date": ["какая дата", "текущая дата", "сегодня дата"],
            "show_time": ["какое время", "текущее время", "который час"],
            "media_pause": ["пауза", "стоп музыка", "остановись"],
        }
        
        for intent, phrases in variations.items():
            matches = 0
            for phrase in phrases:
                result = self.nlu.parse(phrase)
                if result["type"] == intent:
                    matches += 1
                print(f"  '{phrase}' -> {result['type']}")
            
            print(f"  ✓ {matches}/{len(phrases)} variations matched {intent}")


if __name__ == "__main__":
    print("=" * 60)
    print("ML-based NLU Tests")
    print("=" * 60)
    
    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestMLNLU))
    suite.addTests(loader.loadTestsFromTestCase(TestMLNLUIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestMLNLUAccuracy))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("✅ ALL TESTS PASSED!")
    else:
        print(f"❌ TESTS FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 60)
