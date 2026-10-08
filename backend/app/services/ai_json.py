"""Shared helpers for parsing JSON objects out of LLM text output."""
from __future__ import annotations

import json
import re

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> object:
    """从 LLM 文本提取 JSON 对象（多级容错）。

    依次尝试: 整段 → markdown 围栏内 → 首个 {...} 平衡块;
    每级再对 尾随垃圾 / 尾逗号 做轻量修复。全部失败才报错。
    """
    source = text or ""
    candidates: list[str] = []
    stripped = source.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(
        match.group(1).strip() for match in _FENCED_JSON_RE.finditer(source)
    )
    brace = _first_brace_block(source)
    if brace and brace.strip() not in candidates:
        candidates.append(brace)
    last_error: Exception | None = None
    for candidate in candidates:
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
        try:
            json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = e
    raise ValueError(f"AI 返回的不是合法 JSON: {last_error}")


def _try_parse_json(candidate: str) -> object | None:
    """尽力解析一段可能带尾随垃圾 / 尾逗号的 JSON；失败返 None。"""
    variants = [candidate.strip()]
    last = candidate.rfind("}")
    if last >= 0 and last < len(candidate) - 1:
        variants.append(candidate[:last + 1].strip())
    for v in variants:
        if not v:
            continue
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
        # 去掉数组/对象结尾的多余逗号 (AI 常见错误): `,}` / `,]`
        cleaned = re.sub(r",\s*([}\]])", r"\1", v)
        if cleaned != v:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
    return None


def _first_brace_block(text: str) -> str:
    """括号配对截取首个 {...} 块（AI 偶尔混入前后解释文字时的兜底）。"""
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


extract_json_object = _extract_json_object
