"""Pluggable model interfaces: embeddings, language models and speech.

Must not depend on Lekha business modules (CLAUDE.md §4.3).
"""

from model_layer.bge_m3 import BgeM3Embedder
from model_layer.embeddings import (
    DIMENSIONS,
    Embedder,
    FakeEmbedder,
    ModelNotAvailableError,
)
from model_layer.llm import (
    BedrockClaudeModel,
    LanguageModel,
    LanguageModelError,
    LlmRequest,
    LlmResponse,
    OllamaModel,
)
from model_layer.minilm import MiniLmEmbedder
from model_layer.speech import (
    FAKE_AUDIO_TYPE,
    MAX_AUDIO_BYTES,
    Audio,
    FakeSpeechToText,
    FakeTextToSpeech,
    SpeechError,
    SpeechToText,
    TextToSpeech,
    Transcript,
    UnsupportedAudioError,
)

__all__ = [
    "DIMENSIONS",
    "FAKE_AUDIO_TYPE",
    "MAX_AUDIO_BYTES",
    "Audio",
    "BedrockClaudeModel",
    "BgeM3Embedder",
    "Embedder",
    "FakeEmbedder",
    "FakeSpeechToText",
    "FakeTextToSpeech",
    "LanguageModel",
    "LanguageModelError",
    "LlmRequest",
    "LlmResponse",
    "MiniLmEmbedder",
    "ModelNotAvailableError",
    "OllamaModel",
    "SpeechError",
    "SpeechToText",
    "TextToSpeech",
    "Transcript",
    "UnsupportedAudioError",
]
