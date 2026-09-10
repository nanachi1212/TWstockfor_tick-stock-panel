# 前端二次開發目錄

在獨立子目錄中創建 `extension.tsx`，構建時會自動發現，無需修改核心路由和導航文件。

```text
frontend/src/custom/<namespace>/extension.tsx
```

以 [`_template/extension.tsx.example`](_template/extension.tsx.example) 為起點，並遵循 [`docs/secondary-development.md`](../../../docs/secondary-development.md)。模板文件不會參與構建。
