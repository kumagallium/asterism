"""AI を役割で 2 つに（design §21）— 「作る AI」と「答える AI」の LLM ヘッダ。

ブラウザはいつもの ``X-LLM-*``（＝作る AI）を送り、答える AI が別のモデルのときだけ
``X-LLM-Answer-*`` を足す。サーバは呼び出しの役割で組を選ぶ:

* 作る（既定）: ``X-LLM-*`` のまま。役割を書き換えていない経路はすべてこちら
* 答える: ``X-LLM-Answer-Provider`` があれば ``X-LLM-Answer-*`` **だけ**で組む
  （作る側の鍵・base を混ぜない）。無ければ ``X-LLM-*``（＝今と同じ）

:func:`answer_headers` は答える AI の組を ``X-LLM-*`` の名前に畳む。別プロセスの Ask
（demo-agent）に渡す前に使えば、demo-agent は役割を知らずに済む。
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["ANSWER_HEADERS", "PLAIN_HEADERS", "answer_headers", "has_answer_role"]

#: 作る AI（既定）の組 — 小文字（Starlette の headers は大小を問わない）
PLAIN_HEADERS = ("x-api-key", "x-llm-provider", "x-llm-model", "x-llm-api-base", "x-llm-max-tokens")
#: 答える AI の組。並びは PLAIN_HEADERS と対応する
ANSWER_HEADERS = (
    "x-llm-answer-key",
    "x-llm-answer-provider",
    "x-llm-answer-model",
    "x-llm-answer-api-base",
    "x-llm-answer-max-tokens",
)


def _get(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:  # ふつうの dict は大小を区別する
        value = next((v for k, v in headers.items() if k.lower() == name), None)
    return value or None


def has_answer_role(headers: Mapping[str, str]) -> bool:
    """答える AI が作る AI と別に指定されているか。"""
    return _get(headers, "x-llm-answer-provider") is not None


def answer_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """答える AI の組を ``X-LLM-*`` の名前で返す（空の値は落とす）。"""
    names = ANSWER_HEADERS if has_answer_role(headers) else PLAIN_HEADERS
    out: dict[str, str] = {}
    for plain, src in zip(PLAIN_HEADERS, names, strict=True):
        value = _get(headers, src)
        if value:
            out[plain] = value
    return out
