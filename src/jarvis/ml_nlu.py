"""
ML-based NLU using spaCy for intent classification and entity extraction.
Hybrid approach: uses spaCy embeddings + pattern matching for efficient NLU.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import spacy
from spacy.vocab import Vocab
import numpy as np

from .nlu import extract_number

logger = logging.getLogger(__name__)

SITE_ALIASES = {
    "youtube": "www.youtube.com",
    "you tube": "www.youtube.com",
    "ютуб": "www.youtube.com",
    "ютюб": "www.youtube.com",
    "ютьюб": "www.youtube.com",
    "ютубе": "www.youtube.com",
    "яндекс": "yandex.ru",
    "гугл": "google.com",
    "google": "google.com",
    "github": "github.com",
    "гитхаб": "github.com",
}

# Training data: (text, {"intents": [...], "slots": {...}})
TRAINING_DATA = [
    # Browser commands
    ("перейди на гугл", {"intents": ["browser_navigate"], "slots": {"url": "гугл"}}),
    ("открой сайт яндекс", {"intents": ["browser_navigate"], "slots": {"url": "яндекс"}}),
    ("гугл котики", {"intents": ["browser_search"], "slots": {"query": "котики"}}),
    ("поиск погода в москве", {"intents": ["browser_search"], "slots": {"query": "погода в москве"}}),
    ("найти информацию про питон", {"intents": ["browser_search"], "slots": {"query": "информацию про питон"}}),
    
    # Media commands
    ("включи музыку", {"intents": ["media_play"], "slots": {}}),
    ("включи музик", {"intents": ["media_play"], "slots": {}}),
    ("запусти музыку", {"intents": ["media_play"], "slots": {}}),
    ("пауза", {"intents": ["media_pause"], "slots": {}}),
    ("стоп музыка", {"intents": ["media_pause"], "slots": {}}),
    ("остановись", {"intents": ["media_pause"], "slots": {}}),
    ("далее", {"intents": ["media_next"], "slots": {}}),
    ("следующая песня", {"intents": ["media_next"], "slots": {}}),
    ("следующий трек", {"intents": ["media_next"], "slots": {}}),
    ("назад", {"intents": ["media_previous"], "slots": {}}),
    ("предыдущая песня", {"intents": ["media_previous"], "slots": {}}),
    ("предыдущий трек", {"intents": ["media_previous"], "slots": {}}),
    
    # Open apps
    ("открой браузер", {"intents": ["open_app"], "slots": {"target": "браузер"}}),
    ("запусти хром", {"intents": ["open_app"], "slots": {"target": "браузер"}}),
    ("запусти телеграм", {"intents": ["open_app"], "slots": {"target": "телеграм"}}),
    ("открой вс код", {"intents": ["open_app"], "slots": {"target": "vscode"}}),
    ("запусти блокнот", {"intents": ["open_app"], "slots": {"target": "блокнот"}}),
    
    # Volume
    ("сделай тише на 10", {"intents": ["volume_down"], "slots": {"delta": 10}}),
    ("убавь громкость на 20", {"intents": ["volume_down"], "slots": {"delta": 20}}),
    ("сделай громче на 15", {"intents": ["volume_up"], "slots": {"delta": 15}}),
    ("добавь громкость на 5", {"intents": ["volume_up"], "slots": {"delta": 5}}),
    ("громкость 50", {"intents": ["set_volume"], "slots": {"value": 50}}),
    ("установи звук на 80", {"intents": ["set_volume"], "slots": {"value": 80}}),
    ("поставь громкость на 50", {"intents": ["set_volume"], "slots": {"value": 50}}),
    ("громкость на 30", {"intents": ["set_volume"], "slots": {"value": 30}}),
    ("громкость на 70", {"intents": ["set_volume"], "slots": {"value": 70}}),
    
    # Scenarios
    ("рабочий режим", {"intents": ["run_scenario"], "slots": {"name": "рабочий режим"}}),
    ("включи рабочий режим", {"intents": ["run_scenario"], "slots": {"name": "рабочий режим"}}),
    
    # Folder operations
    ("создай папку проект", {"intents": ["create_folder"], "slots": {"name": "проект"}}),
    ("сделай папку документы", {"intents": ["create_folder"], "slots": {"name": "документы"}}),
    
    # Time/Date
    ("какая дата", {"intents": ["show_date"], "slots": {}}),
    ("сегодня дата", {"intents": ["show_date"], "slots": {}}),
    ("текущая дата", {"intents": ["show_date"], "slots": {}}),
    ("какое время", {"intents": ["show_time"], "slots": {}}),
    ("текущее время", {"intents": ["show_time"], "slots": {}}),
    ("который час", {"intents": ["show_time"], "slots": {}}),
    
    # Notes and reminders
    ("запомни купить хлеб", {"intents": ["add_note"], "slots": {"text": "купить хлеб"}}),
    ("запомни позвонить маме", {"intents": ["add_note"], "slots": {"text": "позвонить маме"}}),
    ("напоминание в 5 часов", {"intents": ["create_reminder"], "slots": {"time": "5 часов"}}),
    ("напоминание в три часа дня", {"intents": ["create_reminder"], "slots": {"time": "три часа"}}),
    ("вспомни что нужно было", {"intents": ["read_notes"], "slots": {}}),
    ("что я напоминал себе", {"intents": ["read_notes"], "slots": {}}),
]


class MLNLU:
    """Hybrid ML-based NLU using spaCy embeddings + pattern matching."""
    
    def __init__(self, model_name: str = "ru_core_news_sm", wake_word: str = "джарвис"):
        """Initialize ML NLU.
        
        Args:
            model_name: Name of spaCy model to load
            wake_word: Wake word to strip from input (default: 'джарвис')
        """
        self.training_data = TRAINING_DATA
        self.intent_map: Dict[str, Dict] = {}  # Maps intent to training examples
        self.intent_vectors: Dict[str, np.ndarray] = {}  # Intent embeddings
        self.wake_word = wake_word.lower()
        
        # Load spaCy model
        logger.info(f"Loading spaCy model: {model_name}")
        try:
            self.nlp = spacy.load(model_name)
        except OSError:
            logger.error(f"Model {model_name} not found. Please install it with:")
            logger.error(f"  python -m spacy download {model_name}")
            self.nlp = spacy.blank("ru")
        
        # Build intent database from training data
        self._build_intent_map()
        self._compute_intent_vectors()
    
    def load_config(self):
        """Перезагружает конфигурацию (для совместимости с engine.reload_config)"""
        # ML NLU не использует config.json напрямую, но метод нужен для единообразия
        pass
    
    def _build_intent_map(self):
        """Build mapping of intents to training examples."""
        for text, meta in self.training_data:
            for intent in meta.get("intents", []):
                if intent not in self.intent_map:
                    self.intent_map[intent] = []
                self.intent_map[intent].append({
                    "text": text,
                    "slots": meta.get("slots", {})
                })
        
        logger.debug(f"Built intent map: {len(self.intent_map)} intents")
    
    def _compute_intent_vectors(self):
        """Compute average embedding vectors for each intent."""
        for intent, examples in self.intent_map.items():
            vectors = []
            for ex in examples:
                doc = self.nlp(ex["text"])
                if doc.has_vector:
                    vectors.append(doc.vector)
            
            if vectors:
                self.intent_vectors[intent] = np.mean(vectors, axis=0)
            else:
                self.intent_vectors[intent] = np.zeros(96)  # Default vector size for ru_core_news_sm
        
        logger.debug(f"Computed vectors for {len(self.intent_vectors)} intents")
    
    def parse(self, text: str) -> Dict:
        """Parse user input and return intent with slots.
        
        Args:
            text: User input text (may include wake word)
            
        Returns:
            Dictionary with "type" (intent), "slots" (extracted values), and "confidence"
        """
        text_lower = text.lower()
        
        # Strip wake word if present
        text_clean = self._strip_wake_word(text_lower)

        # Rule-based fast path for offline site opening commands
        # Examples: "включи youtube", "открой ютуб", "запусти github"
        site_url = self._resolve_site_url(text_clean)
        if site_url and self._is_open_site_command(text_clean):
            return {
                "type": "browser_navigate",
                "slots": {"url": site_url},
                "confidence": 0.99,
            }
        
        # Try exact match first
        for intent, examples in self.intent_map.items():
            for ex in examples:
                if ex["text"] == text_clean:
                    logger.debug(f"Exact match: {text} -> {intent}")
                    return {
                        "type": intent,
                        "slots": ex["slots"].copy(),
                        "confidence": 1.0
                    }
        
        # Get embedding for input
        doc = self.nlp(text_clean)
        if not doc.has_vector:
            logger.warning(f"No vector for: {text}")
            return {"type": "unknown", "slots": {}, "confidence": 0.0}
        
        # Find most similar intent using cosine similarity
        best_intent = None
        best_score = 0.0
        best_example = None
        
        for intent, examples in self.intent_map.items():
            for ex in examples:
                # Compute similarity
                ex_doc = self.nlp(ex["text"])
                if ex_doc.has_vector:
                    similarity = self._cosine_similarity(doc.vector, ex_doc.vector)
                    if similarity > best_score:
                        best_score = similarity
                        best_intent = intent
                        best_example = ex
        
        if best_intent is None or best_score < 0.3:
            logger.debug(f"No intent found for: {text}")
            return {"type": "unknown", "slots": {}, "confidence": 0.0}
        
        # Extract slots
        slots = self._extract_slots(text_clean, best_intent, best_example)

        if not self._is_plausible_intent(text_clean, best_intent, slots):
            logger.debug(f"Intent rejected by plausibility filter: {best_intent} for '{text_clean}'")
            return {"type": "unknown", "slots": {}, "confidence": float(best_score)}
        
        logger.debug(f"Parsed '{text}' -> {best_intent} (conf: {best_score:.2f})")
        
        return {
            "type": best_intent,
            "slots": slots,
            "confidence": float(best_score)
        }

    def _is_plausible_intent(self, text: str, intent: str, slots: Dict) -> bool:
        """Guardrail to reduce false positives for ambiguous phrases."""
        text_lower = text.lower()

        if intent == "add_note":
            return any(keyword in text_lower for keyword in ("запомни", "заметка", "запиши")) and bool(slots.get("text"))

        if intent == "create_reminder":
            return "напомин" in text_lower and bool(slots.get("time"))

        if intent == "read_notes":
            return any(keyword in text_lower for keyword in ("вспомни", "заметки", "прочитай заметки"))

        if intent == "open_app":
            return bool(slots.get("target"))

        if intent == "browser_navigate":
            return bool(slots.get("url"))

        if intent == "browser_search":
            return bool(slots.get("query"))

        if intent == "set_volume":
            return slots.get("value") is not None

        if intent in ("volume_up", "volume_down"):
            return slots.get("delta") is not None

        if intent == "show_date":
            return any(keyword in text_lower for keyword in ("дата", "число", "сегодня"))

        if intent == "show_time":
            return any(keyword in text_lower for keyword in ("время", "час"))

        return True

    def _is_open_site_command(self, text: str) -> bool:
        open_keywords = (
            "открой",
            "зайди",
            "перейди",
            "запусти",
            "включи",
        )
        return any(keyword in text for keyword in open_keywords)

    def _resolve_site_url(self, text: str) -> Optional[str]:
        for alias, url in SITE_ALIASES.items():
            if alias in text:
                return url
        return None
    
    def _strip_wake_word(self, text: str) -> str:
        """Remove wake word from text if present.
        
        Args:
            text: Input text (lowercase)
            
        Returns:
            Text without wake word
        """
        if not text or self.wake_word not in text:
            return text
        
        # Pattern 1: "джарвис команда" or "джарвис, команда"
        patterns = [
            (f"{self.wake_word},", " "),  # "джарвис," -> remove with space
            (f"{self.wake_word} ", " "),  # "джарвис " -> remove with space
            (self.wake_word, ""),         # "джарвис" -> just remove
        ]
        
        text_clean = text
        for old, new in patterns:
            if old in text_clean:
                text_clean = text_clean.replace(old, new, 1)  # Replace only first occurrence
                text_clean = text_clean.strip()
                logger.debug(f"Stripped wake word: '{text}' -> '{text_clean}'")
                return text_clean
        
        return text_clean
    
    def parse_with_wake_word(self, text: str) -> Dict:
        """Parse input that may contain both wake word and command.
        
        This method handles cases like:
        - "джарвис поставь громкость на 50"
        - "джарвис, включи музыку"
        - "привет джарвис включи свет"
        
        Args:
            text: User input text
            
        Returns:
            Dictionary with "type" (intent), "slots", "confidence", and "wake_word_detected"
        """
        text_lower = text.lower()
        
        # Check if wake word is present
        wake_word_detected = self.wake_word in text_lower
        
        # Parse the full text or cleaned text
        result = self.parse(text_lower)
        result["wake_word_detected"] = wake_word_detected
        
        return result
    
    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return float(np.dot(a, b) / (norm_a * norm_b))
    
    def _extract_slots(self, text: str, intent: str, example: Dict) -> Dict:
        """Extract slot values based on intent and patterns.
        
        Args:
            text: User input text
            intent: Detected intent
            example: Matching training example
            
        Returns:
            Dictionary of extracted slots
        """
        slots = example.get("slots", {}).copy()

        if intent in {"add_note", "create_reminder", "read_notes", "browser_search", "browser_navigate"}:
            slots = {}
        
        # Try to extract values from text
        if "url" in slots or "browser_navigate" in intent:
            # Extract URL/site name
            for keyword in ["на", "сайт", "открой"]:
                if keyword in text:
                    value = text.split(keyword)[-1].strip()
                    if value:
                        slots["url"] = value
                    break
        
        elif "query" in slots or "search" in intent:
            # Extract search query
            for keyword in ["гугл", "поиск", "найти"]:
                if keyword in text:
                    value = text.split(keyword)[-1].strip()
                    if value:
                        slots["query"] = value
                    break
        
        elif "volume" in intent:
            # Extract volume delta/value using extract_number (handles both digits and words)
            value = extract_number(text)
            if value is not None:
                if "down" in intent or "тише" in text or "убавь" in text:
                    slots["delta"] = value
                elif "up" in intent or "громче" in text or "добавь" in text:
                    slots["delta"] = value
                else:
                    slots["value"] = value
        
        elif intent == "create_folder":
            # Extract folder name
            import re
            match = re.search(r"(?:создай|сделай)\s+папк[ауы]\s+(.+)", text)
            if match:
                slots["name"] = match.group(1).strip()
        
        elif intent in ["add_note", "create_reminder"]:
            # Extract note/reminder text
            import re
            match = re.search(r"(?:запомни|запиши|напоминание|напомни)\s+(.+)", text)
            if match:
                slot_key = "text" if intent == "add_note" else "time"
                slots[slot_key] = match.group(1).strip()
        
        elif intent == "open_app":
            # Extract app name
            apps = ["браузер", "хром", "телеграм", "вс код", "блокнот"]
            for app in apps:
                if app in text:
                    if app in ["хром"]:
                        slots["target"] = "браузер"
                    else:
                        slots["target"] = app
                    break
        
        return slots
