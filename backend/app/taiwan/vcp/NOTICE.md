# 來源與授權

本目錄的 VCP 偵測與評分邏輯移植自：

- 專案：https://github.com/tradermonty/claude-trading-skills
- 路徑：`skills/vcp-screener/`（`scripts/screen_vcp.py`、`scripts/calculators/*.py`、`scripts/scorer.py`、
  `references/vcp_methodology.md`、`references/scoring_system.md`）
- 授權：MIT

只移植偵測與評分規則，未移植 FMP client、API 呼叫、歷史回測與報告產生器；資料改讀本機 TWSE/TPEX 日線。

---

MIT License

Copyright (c) 2026 TraderMonty

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
