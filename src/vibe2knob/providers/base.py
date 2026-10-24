from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Union


class ProviderError(RuntimeError):
    """The model could not produce an answer (missing key, refusal, bad output...)."""


@dataclass
class ToolSpec:
    """A tool the model may call. `strict` asks the provider to enforce the schema."""

    name: str
    description: str
    input_schema: dict
    strict: bool = True


# What stream_chat yields. A stream ends with exactly one Done or Error.


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    name: str
    input: dict
    id: str | None = None


@dataclass
class Phase:
    """What the model is on, for a progress line. Optional: "steps" when it starts a tool call,
    with that tool's name when the provider knows it."""

    name: str
    tool: str | None = None


@dataclass
class Done:
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None
    # The provider's own record of this answer (Claude: its content blocks, thinking
    # included), passed back unchanged as the assistant message's `raw` in the next round.
    raw: Any = None


@dataclass
class Error:
    message: str


StreamEvent = Union[TextDelta, ToolCall, Phase, Done, Error]


class Provider(Protocol):
    """An LLM backend.

    `generate` returns JSON text matching `schema` (the one-shot `ask` path).
    `stream_chat` streams a chat answer: prose deltas, then any tool calls, then Done. It may
    also yield Phase("steps") when the model starts writing a tool call, so the client can say
    so before the call arrives.
    Messages are plain {"role": "user" | "assistant", "content": str} dicts. Within a turn
    that called read tools, two more shapes follow the user's message, round by round:
    - {"role": "assistant", "content": prose, "tool_calls": [{"id", "name", "input"}],
       "raw": Done.raw of that round}
    - {"role": "user", "content": "", "tool_results": [{"id", "name", "content": str}]},
      with one result for every call in the assistant message before it.
    `generate_text` is a cheap plain-text call, used for chat titles.

    Usage: Done.usage holds the call's token counts (input_tokens, output_tokens, and where
    the backend reports them cache_read_input_tokens, cache_creation_input_tokens,
    thinking_tokens and the `model` that answered). `generate` and `generate_text` leave
    theirs in `last_usage`. Two optional methods help the usage split: `count_parts(system,
    tools)` gives exact token counts of the instructions and tool definitions, and
    `context_window()` the model's window; usage.py copes without them.
    """

    name: str
    last_usage: dict | None

    def generate(self, system: str, user: str, schema: dict) -> str: ...

    def stream_chat(
        self, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> Iterator[StreamEvent]: ...

    def generate_text(self, system: str, user: str) -> str: ...
