"""A deterministic stand-in language model for tests. SIMULATION ONLY: never a real model.

It reads the numbered sources out of the prompt, picks the sentence sharing the most words
with the question, and answers by quoting it with a citation. It can be told to misbehave,
and it records every prompt it was sent, so tests can prove what the model was (and was
not) shown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from lekha.generation import MINUTES_SYSTEM_PROMPT
from lekha.generation.prompt import FENCE_CLOSE, FENCE_OPEN
from model_layer import LanguageModelError, LlmRequest, LlmResponse

_SOURCE = re.compile(r"\[(\d+)\] title:[^\n]*\n<<<source>>>\n(.*?)\n<<<endsource>>>", re.S)
_QUESTION = re.compile(r"User question \(language: \w+\): (.*)\Z", re.S)
_WORD = re.compile(r"\w+", re.UNICODE)
_SENTENCE = re.compile(r"[^.!?।]+[.!?।]?")
_TIME_PREFIX = re.compile(r"^\[[\d:]+\]\s*")
_SPEAKER_OF = re.compile(r"^([^:]{1,60}):\s")
_TURN = re.compile(r"^(\[[\d:]+\]\s*)?([^:]{1,60}:\s)?")


class Behaviour(StrEnum):
    EXTRACTIVE = "extractive"  # honest: quote the best sentence, abstain if nothing matches
    INVENT = "invent"  # cite a quotation that is not in the source
    BROKEN = "broken"  # reply with something that isn't JSON
    DOWN = "down"  # the provider is unreachable


@dataclass
class FakeLanguageModel:
    behaviour: Behaviour = Behaviour.EXTRACTIVE
    model_name: str = "fake-extractive-v1"
    prompts: list[str] = field(default_factory=list)

    async def generate(self, request: LlmRequest) -> LlmResponse:
        self.prompts.append(request.user)
        if self.behaviour is Behaviour.DOWN:
            raise LanguageModelError("fake model is down")
        if self.behaviour is Behaviour.BROKEN:
            return self._reply("I think the answer is twenty days.")
        if request.system == MINUTES_SYSTEM_PROMPT:
            return self._reply(json.dumps(self._minutes(request.user), ensure_ascii=False))
        number, sentence = self._best(request.user)
        if number is None:
            body = {"answer": "", "citations": [], "abstained": True}
        elif self.behaviour is Behaviour.INVENT:
            body = {
                "answer": f"It is forty-two days [{number}].",
                "citations": [{"source": number, "span": "forty-two days of extra leave"}],
                "abstained": False,
            }
        else:
            body = {
                "answer": f"{sentence} [{number}]",
                "citations": [{"source": number, "span": sentence}],
                "abstained": False,
            }
        return self._reply(json.dumps(body, ensure_ascii=False))

    async def close(self) -> None:
        return None

    def _reply(self, text: str) -> LlmResponse:
        return LlmResponse(text=text, model=self.model_name, input_tokens=None, output_tokens=None)

    def _minutes(self, prompt: str) -> dict[str, object]:
        """Decisions are lines saying "agreed"/"approve"; actions are lines with "will" or
        "must". Evidence is the spoken text itself (or, misbehaving, something invented)."""
        body = prompt.split(FENCE_OPEN, 1)[-1].split(FENCE_CLOSE, 1)[0].strip("\n")
        decisions, actions = [], []
        for line in body.splitlines():
            spoken = _TURN.sub("", line).strip()
            speaker = _SPEAKER_OF.match(_TIME_PREFIX.sub("", line))
            evidence = (
                spoken if self.behaviour is Behaviour.EXTRACTIVE else "we decided on a holiday"
            )
            lowered = spoken.lower()
            if "agreed" in lowered or "approve" in lowered:
                decisions.append({"text": spoken, "evidence": evidence})
            if " will " in lowered or " must " in lowered:
                actions.append(
                    {
                        "task": spoken,
                        "owner": speaker.group(1) if speaker else "",
                        "due": "",
                        "evidence": evidence,
                    }
                )
        return {
            "summary": "Synthetic summary of the meeting.",
            "decisions": decisions,
            "action_items": actions,
        }

    @staticmethod
    def _best(prompt: str) -> tuple[int | None, str]:
        question_match = _QUESTION.search(prompt)
        wanted = set(_WORD.findall(question_match.group(1).lower())) if question_match else set()
        best: tuple[int, int | None, str] = (0, None, "")
        for number, text in _SOURCE.findall(prompt):
            for sentence in _SENTENCE.findall(text):
                overlap = len(wanted & set(_WORD.findall(sentence.lower())))
                if overlap > best[0]:
                    best = (overlap, int(number), sentence.strip())
        return best[1], best[2]
