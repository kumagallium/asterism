"""list_available_models — the model-picker (#②) listing.

``anthropic`` / ``openai`` are optional deps (absent in the base test venv), so
we inject fakes into ``sys.modules``. This also pins the lazy-import contract and
the exact SDK call shapes (anthropic: iterable ``models.list()``; openai:
``models.list().data``).
"""

from __future__ import annotations

import sys
import types
from typing import ClassVar

import pytest

from asterism_step0.llm import list_available_models


class _AModel:
    def __init__(self, id_: str, display: str | None = None) -> None:
        self.id = id_
        self.display_name = display


class _AModels:
    def __init__(self, items: list[_AModel]) -> None:
        self._items = items

    def list(self) -> list[_AModel]:  # anthropic: models.list() is iterable
        return self._items


class _FakeAnthropic:
    last: ClassVar[dict] = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeAnthropic.last = kwargs
        self.models = _AModels(
            [_AModel("claude-opus-4-8", "Claude Opus 4.8"), _AModel("claude-x")]
        )


class _OModel:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _OList:
    def __init__(self, data: list[_OModel]) -> None:
        self.data = data


class _OModels:
    def __init__(self, data: list[_OModel]) -> None:
        self._data = data

    def list(self) -> _OList:  # openai: models.list().data
        return _OList(self._data)


class _FakeOpenAI:
    last: ClassVar[dict] = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeOpenAI.last = kwargs
        self.models = _OModels([_OModel("gpt-4o"), _OModel("gpt-4o-mini")])


def _inject(monkeypatch: pytest.MonkeyPatch, name: str, attr: str, cls: type) -> None:
    mod = types.ModuleType(name)
    setattr(mod, attr, cls)
    monkeypatch.setitem(sys.modules, name, mod)


def test_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, "anthropic", "Anthropic", _FakeAnthropic)
    out = list_available_models("anthropic", api_key="sk")
    by_id = {m["id"]: m["display_name"] for m in out}
    assert by_id == {"claude-opus-4-8": "Claude Opus 4.8", "claude-x": "claude-x"}
    assert _FakeAnthropic.last == {"api_key": "sk"}


def test_openai_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, "openai", "OpenAI", _FakeOpenAI)
    out = list_available_models(
        "openai-compatible", api_key="sk", api_base="https://api.x/v1"
    )
    assert [m["id"] for m in out] == ["gpt-4o", "gpt-4o-mini"]
    assert _FakeOpenAI.last == {"api_key": "sk", "base_url": "https://api.x/v1"}


def test_openai_default_base(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, "openai", "OpenAI", _FakeOpenAI)
    list_available_models("openai", api_key="sk")
    assert _FakeOpenAI.last["base_url"] is None


def test_unknown_provider() -> None:
    with pytest.raises(ValueError):
        list_available_models("gemini")
