import { isRouteErrorResponse, useRouteError } from 'react-router-dom'

/** 頁面出錯時的畫面：一定有「重新整理」和「回看板」，不會卡在沒有返回的錯誤頁。 */
export function RouteError() {
  const error = useRouteError()
  const message = isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : error instanceof Error ? error.message : String(error)
  const stale = /dynamically imported module|Importing a module script failed|Failed to fetch/i.test(message)
  return (
    <div role="alert" className="mx-auto mt-16 max-w-lg space-y-3 rounded-2xl border border-border bg-surface p-5 text-sm text-foreground">
      <h1 className="text-base font-semibold">{stale ? '看板已更新，請重新整理' : '這個頁面出錯了'}</h1>
      <p className="text-xs text-muted">
        {stale ? '程式剛更新過，這個視窗還在用舊版的檔案。重新整理後就會載入新版。' : '可以先重新整理；若仍出錯，回看板再試一次。'}
      </p>
      <p className="break-words rounded-md bg-base p-2 font-mono text-[11px] text-muted">{message}</p>
      <div className="flex gap-2">
        <button type="button" onClick={() => window.location.reload()} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white">重新整理</button>
        <button type="button" onClick={() => { window.location.href = '/' }} className="rounded-lg border border-border px-3 py-1.5 text-xs">回看板</button>
      </div>
    </div>
  )
}
