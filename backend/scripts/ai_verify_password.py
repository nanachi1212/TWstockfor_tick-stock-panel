"""產生或撤銷驗證專用密碼 (本機自動化驗證登入用, 只在本機請求時接受)。

用法 (從 backend/ 目錄運行):
    uv run python -m scripts.ai_verify_password          # 產生新密碼 (舊的失效)
    uv run python -m scripts.ai_verify_password --clear  # 撤銷

明文只寫進 data/user_data/ai_verify_password.txt, 不印到終端機。
"""
from __future__ import annotations

import argparse
import contextlib
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
    path.write_text(password, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    print(f"驗證專用密碼已更新, 明文在 {path}")


if __name__ == "__main__":
    main()
