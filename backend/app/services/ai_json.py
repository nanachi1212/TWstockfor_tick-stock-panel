"""Shared helpers for parsing JSON objects out of LLM text output."""
from __future__ import annotations

import json
import re

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> object:
    """從 LLM 文本提取 JSON 物件（多級容錯）。

    依次嘗試: 整段 → markdown 圍欄內 → 首個 {...} 平衡塊;
    每級再對 尾隨垃圾 / 尾逗號 做輕量修復。全部失敗才出錯。
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
    raise ValueError(f"AI 回傳的不是合法 JSON: {last_error}")


def _try_parse_json(candidate: str) -> object | None:
    """盡力解析一段可能帶尾隨垃圾 / 尾逗號的 JSON；失敗返 None。"""
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
        # 去掉陣列/物件結尾的多餘逗號 (AI 常見錯誤): `,}` / `,]`
        cleaned = re.sub(r",\s*([}\]])", r"\1", v)
        if cleaned != v:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
    return None


def _first_brace_block(text: str) -> str:
    """括號配對截取首個 {...} 塊（AI 偶爾混入前後解釋文字時的保底）。"""
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
