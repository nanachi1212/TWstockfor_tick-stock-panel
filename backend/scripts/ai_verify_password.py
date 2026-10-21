"""產生或撤銷驗證專用密碼 (本機自動化驗證登入用, 只在本機請求時接受)。

用法 (從 backend/ 目錄運行):
    uv run python -m scripts.ai_verify_password          # 產生新密碼 (舊的失效)
    uv run python -m scripts.ai_verify_password --clear  # 撤銷

明文只寫進 data/user_data/ai_verify_password.txt, 不印到終端機。
"""
from __future__ import annotations

import argparse
import os
import secrets

from app.config import settings
from app.services import auth


def main() -> None:
    parser = argparse.ArgumentParser(description="產生或撤銷驗證專用密碼")
    parser.add_argument("--clear", action="store_true", help="撤銷驗證專用密碼")
    args = parser.parse_args()
    path = settings.data_dir / "user_data" / "ai_verify_password.txt"
    if args.clear:
        auth.set_verify_password(None)
        path.unlink(missing_ok=True)
        print("驗證專用密碼已撤銷")
        return
    password = secrets.token_urlsafe(24)
    auth.set_verify_password(password)
    path.unlink(missing_ok=True)
    # 建檔當下就是 0600, 不留其他本機使用者可讀的空窗。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(password)
    print(f"驗證專用密碼已更新, 明文在 {path}")


if __name__ == "__main__":
    main()
