; ===========================================================================
; Tick Stock Panel — Inno Setup 安裝包腳本
; ===========================================================================
; 用途: 把 PyInstaller 產出的 dist/NanachiStockPanel/ 文件夾封裝成
;       單個 Setup.exe 安裝程序 (雙擊→安裝精靈→快捷方式→可卸載)。
;
; 構建 (本地):
;   1. 先跑 PyInstaller: cd backend && uv run pyinstaller ../packaging/tickflow.spec
;   2. 再跑 Inno Setup:   ISCC.exe packaging\tickflow.iss
;   3. 產物: packaging\Output\NanachiStockPanel-Setup-x.x.x.exe
;
; 設計決策:
;   - 裝到用戶目錄 {localappdata}\Programs\ (不彈 UAC, 不需管理員)
;   - 用戶數據存在 {app}\data\ (與程序同處一個總目錄, 視覺直觀)
;   - 卸載時詢問是否刪除用戶數據 ({app}\data\)
;   - 覆蓋安裝(升級)不動 data\: Inno Setup 只寫程序文件, data 不在安裝清單
;   - 桌面 + 開始菜單快捷方式
;   - 卸載入口 (控制面板可見)
; ===========================================================================

#define MyAppName          "Nanachi 的台股監控看板"
#define MyAppNameEN       "Nanachi Stock Panel"
#define MyAppExeName      "NanachiStockPanel.exe"
#define MyAppPublisher    "Nanachi"

; 版本號: 從 frontend/package.json 讀取, 與 Release tag 保持一致
; 手動指定更可靠 (CI 傳入 /DMyAppVersion)
#ifndef MyAppVersion
  #define MyAppVersion     "0.0.0"
#endif

[Setup]
; 基本信息
AppName={#MyAppName}
AppVerName={#MyAppName} {#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 默認裝到 D 盤 (非系統盤), 用戶可在精靈中改任意位置
; 若 D 盤不存在, [Code] 段 InitializeWizard 會自動回退到用戶目錄
DefaultDirName=D:\NanachiStockPanel
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=NanachiStockPanel-Setup-{#MyAppVersion}

; 關鍵: 不需要管理員權限, 永不彈 UAC
; 裝到 D 盤普通目錄 (非 Program Files) 不需要管理員權限
PrivilegesRequired=lowest
; 允許用戶在精靈中自由選擇安裝目錄
DisableDirPage=no

; 壓縮
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; 界面
WizardStyle=modern
DisableWelcomePage=no
DisableReadyPage=no
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; 卸載相關
Uninstallable=yes
CreateUninstallRegKey=yes

[Languages]
; 繁中語言包 ChineseTraditional.isl 內置在 packaging/ 下, 由本倉庫曾使用的
; 簡中翻譯以 OpenCC s2tw 轉換、並改用台灣用語而來 (訊息鍵與 placeholder 未變)。
; 不依賴安裝目錄是否含該檔案 (CI 友好)。
Name: "chinesetrad"; MessagesFile: "ChineseTraditional.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 把 PyInstaller 產出的整個文件夾搬進安裝目錄
; Source 路徑相對於 .iss 文件所在目錄
Source: "..\backend\dist\NanachiStockPanel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 開始菜單
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\解除安裝 {#MyAppName}"; Filename: "{uninstallexe}"

; 桌面 (可選, 由 Task 控制)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安裝完成後啟動應用
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸載前先關閉正在運行的應用 (否則 exe 被佔用刪不掉)
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillApp"

; [UninstallDelete] 故意不刪 {app}:
; 用戶數據在 {app}\data\, 若這裡寫 Type: filesandordirs; Name: "{app}" 會連數據一起刪。
; 卸載默認行為已足夠 —— Inno Setup 會刪除它安裝清單內的所有程序文件, 只留下運行時
; 生成的 data\ 目錄。是否清理 data\ 由下方 [Code] 的卸載詢問邏輯決定。

[Code]
// ── 輔助函數: 判斷目錄是否為空 ─────────────────────────────────
// Inno Setup 內置無 IsDirEmpty, 用 FindFirst/FindNext 自行實現。
// 用於卸載後清理空的 {app} 殼目錄。
function IsDirEmpty(const Dir: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Dir) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          Result := False;
          Break;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

// ── 啟動時: 若 D 盤不存在, 回退默認路徑到用戶目錄 ───────────────
// 避免默認 D:\... 但系統沒 D 盤時精靈顯示無效路徑
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard();
var
  DefaultDir: String;
begin
  // D 盤存在 → 用 D 盤; 否則回退用戶目錄 (無需管理員權限)
  if not DirExists('D:\') then
  begin
    DefaultDir := ExpandConstant('{localappdata}\Programs\NanachiStockPanel');
    WizardForm.DirEdit.Text := DefaultDir;
  end;
end;

// ── 卸載時詢問是否刪除用戶數據 ─────────────────────────────────
// 用戶數據在 {app}\data\ (策略/選股/回測/監控/行情), 與程序同處 {app} 總目錄。
// Inno Setup 卸載默認只刪它裝過的程序文件, data\ 會被保留 (覆蓋安裝/常規卸載都不丟)。
// 這裡僅在用戶明確「徹底卸載」時, 才詢問是否清理 data\ + {app} 空殼。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, AppDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // {app}\data = 用戶數據目錄 (與程序同總目錄, 子文件夾)
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) then
    begin
      if SuppressibleMsgBox(
          '是否同時刪除使用者資料？' + #13#10 + #13#10 +
          '位置：' + DataDir + #13#10 +
          '內容：行情資料、選股結果、回測紀錄、監控規則等' + #13#10 + #13#10 +
          '選「是」徹底解除安裝，選「否」保留資料（重新安裝後可恢復）。',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
    // 清理可能殘留的空 {app} 殼目錄 (程序文件已被 Inno Setup 刪除)
    AppDir := ExpandConstant('{app}');
    if DirExists(AppDir) and IsDirEmpty(AppDir) then
    begin
      DelTree(AppDir, True, True, True);
    end;
  end;
end;
