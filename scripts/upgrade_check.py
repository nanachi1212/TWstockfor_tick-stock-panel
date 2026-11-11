#!/usr/bin/env python3
"""Read-only Git overlap and conflict preview for secondary-development branches."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result


def _lines(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _resolve_repo(path: Path) -> Path:
    result = _git(path, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def _resolve_commit(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _merge_tree_conflicts(root: Path, base: str, current: str, target: str) -> list[str]:
    result = _git(root, "merge-tree", base, current, target)
    lines = result.stdout.splitlines()
    conflicts: list[str] = []
    current_path: str | None = None
    current_conflicted = False
    for line in [*lines, ""]:
        if line in {"changed in both", "added in both", "removed in both"}:
            if current_path and current_conflicted:
                conflicts.append(current_path)
            current_path = None
            current_conflicted = False
            continue
        parts = line.strip().split()
        if current_path is None and len(parts) >= 4 and parts[0] in {"base", "our", "their"}:
            current_path = parts[-1]
        if line.startswith("+<<<<<<< ") or line.startswith("+=======") or line.startswith("+>>>>>>> "):
            current_conflicted = True
    if current_path and current_conflicted:
        conflicts.append(current_path)
    return sorted(set(conflicts))


def _print_paths(title: str, paths: list[str]) -> None:
    print(f"\n{title}: {len(paths)}")
    for path in paths:
        print(f"  - {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="只讀檢查目前二開分支升級到目標 Git ref 時的原始碼重疊與文本衝突",
    )
    parser.add_argument("target", help="目標 Tag/分支/commit, 例如 v0.3.1")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="儲存庫路徑, 預設目前目錄",
    )
    args = parser.parse_args(argv)

    try:
        root = _resolve_repo(args.repo.resolve())
        current = _resolve_commit(root, "HEAD")
        target = _resolve_commit(root, args.target)
        base = _git(root, "merge-base", current, target).stdout.strip()
        if not base:
            raise RuntimeError("目前分支與目標版本沒有共同基線")

        current_paths = _lines(_git(root, "diff", "--name-only", f"{base}..{current}"))
        target_paths = _lines(_git(root, "diff", "--name-only", f"{base}..{target}"))
        overlap = sorted(current_paths & target_paths)
        conflicts = _merge_tree_conflicts(root, base, current, target)
        dirty = sorted(_lines(_git(root, "status", "--short")))

        print("Nanachi 的台股監控看板 二開升級預檢")
        print(f"儲存庫: {root}")
        print(f"目前: {current[:12]}")
        print(f"目標: {args.target} ({target[:12]})")
        print(f"共同基線: {base[:12]}")
        print(f"目前分支修改檔案: {len(current_paths)}")
        print(f"目標版本修改檔案: {len(target_paths)}")
        _print_paths("雙方修改的同一文件", overlap)
        _print_paths("Git 三方預演識別的文本衝突", conflicts)

        if dirty:
            print(f"\n未提交工作區條目: {len(dirty)}")
            print("  注意: 未提交內容不會進入 Git 三方預演, 請先提交到臨時分支後再複檢。")
        if conflicts:
            print("\n結論: 需要人工解決文本衝突並執行完整迴歸。")
            return 2
        if overlap:
            print("\n結論: Git 未發現文本衝突, 但重疊文件需要檢查語義相容和測試。")
            return 0
        print("\n結論: 未發現原始碼重疊; 仍需執行目標版本的驗證矩陣。")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"升級預檢失敗: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
