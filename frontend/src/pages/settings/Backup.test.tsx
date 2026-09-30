import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SettingsBackupPanel } from './Backup'
import { api, type BackupPreview, type DeviceTransferOptions } from '@/lib/api'
import { applyBrowserStorage, restoreBackup } from '@/lib/deviceTransfer'

vi.mock('@/lib/api', () => ({
  api: {
    deviceTransferOptions: vi.fn(),
    deviceTransferPreview: vi.fn(),
    deviceTransferRestore: vi.fn(),
    deviceTransferRollback: vi.fn(),
  },
}))

const OPTIONS: DeviceTransferOptions = {
  categories: [
    { id: 'app_settings', label: 'App 設定', browser_keys: [] },
    { id: 'ui_preferences', label: '介面偏好', browser_keys: ['tf-theme'] },
    { id: 'watchlist', label: '自選股', browser_keys: [] },
    { id: 'portfolio', label: '投資組合', browser_keys: ['portfolio_transactions'] },
  ],
  presets: {
    settings: ['app_settings', 'ui_preferences'],
    settings_portfolio: ['app_settings', 'ui_preferences', 'watchlist', 'portfolio'],
    full: ['app_settings', 'ui_preferences', 'watchlist', 'portfolio'],
  },
  min_password_length: 8,
  file_extension: '.twstock-backup',
}

const PREVIEW: BackupPreview = {
  backup_version: 1,
  app_version: '0.2.0',
  current_app_version: '0.2.1',
  version_match: false,
  created_at: '2026-09-30T01:00:00+00:00',
  source_machine: { os: 'Windows' },
  file_count: 3,
  categories: [
    { id: 'app_settings', label: 'App 設定', file_count: 2, compatible: true, issues: [] },
    { id: 'portfolio', label: '投資組合', file_count: 1, compatible: true, issues: [] },
    { id: 'watchlist', label: '自選股', file_count: 1, compatible: false, issues: ['watchlist.parquet: 欄位結構不符'] },
    { id: 'secrets', label: 'API Keys / Tokens (加密)', file_count: 0, compatible: true, issues: [] },
  ],
  secrets: { included: true, encrypted: true },
  issues: [],
  compatible: false,
}

const fetchMock = vi.fn()

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><SettingsBackupPanel /></QueryClientProvider>)
}

function chooseFile() {
  const file = new File(['zip'], 'home.twstock-backup')
  fireEvent.change(screen.getByLabelText('選擇備份檔'), { target: { files: [file] } })
  return file
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.deviceTransferOptions).mockResolvedValue(OPTIONS)
  vi.stubGlobal('fetch', fetchMock)
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('SettingsBackupPanel — export', () => {
  it('sends selected preset categories and allowlisted localStorage, without secrets by default', async () => {
    localStorage.setItem('portfolio_transactions', '[{"symbol":"2330.TWSE"}]')
    localStorage.setItem('tf-theme', 'light')
    localStorage.setItem('unrelated', 'x')
    // Node Response and jsdom Blob belong to different runtimes; Response.blob() still exercises download.
    fetchMock.mockResolvedValue(new Response('zip', {
      status: 200, headers: { 'Content-Disposition': 'attachment; filename="twstock-1.twstock-backup"' },
    }))
    renderPanel()
    fireEvent.click(await screen.findByLabelText(/設定 \+ 投資組合/))
    await waitFor(() => expect(screen.getByLabelText('投資組合')).toBeChecked())
    fireEvent.click(screen.getByRole('button', { name: /建立備份/ }))

    await screen.findByText('已建立 twstock-1.twstock-backup')
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.objectContaining({ size: 3 }))
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.categories).toEqual(['app_settings', 'ui_preferences', 'watchlist', 'portfolio'])
    expect(body.include_secrets).toBe(false)
    expect(body.password).toBeUndefined()
    expect(body.browser_storage).toEqual({
      ui_preferences: { 'tf-theme': 'light' },
      portfolio: { portfolio_transactions: '[{"symbol":"2330.TWSE"}]' },
    })
  })

  it('requires a matching password before exporting secrets', async () => {
    renderPanel()
    fireEvent.click(await screen.findByLabelText(/包含 API Keys/))
    const button = screen.getByRole('button', { name: /建立備份/ })
    expect(button).toBeDisabled()
    expect(screen.getByText('密碼至少 8 個字元')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('備份密碼'), { target: { value: 'long-password' } })
    fireEvent.change(screen.getByLabelText('再次輸入備份密碼'), { target: { value: 'different' } })
    expect(screen.getByText('兩次輸入的密碼不一致')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('再次輸入備份密碼'), { target: { value: 'long-password' } })
    expect(button).toBeEnabled()
  })

  it('shows backend export errors', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'watchlist.parquet 含本機路徑, 已停止匯出' }), { status: 400 }))
    renderPanel()
    await screen.findByLabelText('App 設定')
    fireEvent.click(screen.getByRole('button', { name: /建立備份/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('含本機路徑')
  })
})

describe('SettingsBackupPanel — import', () => {
  it('previews metadata, version mismatch, incompatible categories and secrets', async () => {
    vi.mocked(api.deviceTransferPreview).mockResolvedValue(PREVIEW)
    renderPanel()
    await screen.findByLabelText('App 設定')
    chooseFile()
    const preview = await screen.findByTestId('backup-preview')
    expect(within(preview).getByText(/與本機 0.2.1 不同/)).toBeInTheDocument()
    expect(within(preview).getByText('包含（需密碼）')).toBeInTheDocument()
    expect(within(preview).getByText(/不相容：watchlist.parquet/)).toBeInTheDocument()
    expect(within(preview).getByLabelText(/自選股/)).toBeDisabled()
    expect(within(preview).getByLabelText(/App 設定/)).toBeChecked()
    expect(within(preview).getByLabelText(/API Keys/)).not.toBeChecked()
  })

  it('restores only confirmed categories and requires password for secrets', async () => {
    vi.mocked(api.deviceTransferPreview).mockResolvedValue(PREVIEW)
    vi.mocked(api.deviceTransferRestore).mockResolvedValue({
      restore_point: 'rp1', categories: [], written: 2, removed: 0,
      browser_storage: { portfolio: { portfolio_transactions: '[]' } },
    })
    renderPanel()
    await screen.findByLabelText('App 設定')
    const file = chooseFile()
    const preview = await screen.findByTestId('backup-preview')
    expect(api.deviceTransferRestore).not.toHaveBeenCalled()  // 預覽不寫入

    fireEvent.click(within(preview).getByLabelText(/API Keys/))
    const confirm = within(preview).getByRole('button', { name: /確認還原/ })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(preview).getByLabelText('備份檔密碼'), { target: { value: 'secret-pass' } })
    fireEvent.click(confirm)

    await screen.findByText(/還原完成：寫入 2 個檔案/)
    expect(api.deviceTransferRestore).toHaveBeenCalledWith(file, ['app_settings', 'portfolio', 'secrets'], 'secret-pass')
    expect(localStorage.getItem('portfolio_transactions')).toBe('[]')
  })

  it('shows wrong-password errors without writing localStorage', async () => {
    localStorage.setItem('portfolio_transactions', 'original')
    vi.mocked(api.deviceTransferPreview).mockResolvedValue(PREVIEW)
    vi.mocked(api.deviceTransferRestore).mockRejectedValue(new Error('密碼錯誤, 或備份檔已被修改'))
    renderPanel()
    await screen.findByLabelText('App 設定')
    chooseFile()
    const preview = await screen.findByTestId('backup-preview')
    fireEvent.click(within(preview).getByLabelText(/API Keys/))
    fireEvent.change(within(preview).getByLabelText('備份檔密碼'), { target: { value: 'bad' } })
    fireEvent.click(within(preview).getByRole('button', { name: /確認還原/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('密碼錯誤')
    expect(localStorage.getItem('portfolio_transactions')).toBe('original')
  })

  it('shows preview errors for invalid files', async () => {
    vi.mocked(api.deviceTransferPreview).mockRejectedValue(new Error('不是有效的 .twstock-backup 檔案'))
    renderPanel()
    await screen.findByLabelText('App 設定')
    chooseFile()
    expect(await screen.findByRole('alert')).toHaveTextContent('不是有效的')
  })
})

describe('deviceTransfer localStorage restore', () => {
  it('replaces only the restored category keys', () => {
    localStorage.setItem('tf-theme', 'dark')
    localStorage.setItem('portfolio_transactions', 'old')
    const snapshot = applyBrowserStorage(OPTIONS, { portfolio: {} })
    expect(localStorage.getItem('portfolio_transactions')).toBeNull()
    expect(localStorage.getItem('tf-theme')).toBe('dark')
    expect(snapshot).toEqual({ portfolio_transactions: 'old' })
  })

  it('rolls back backend files when localStorage write fails', async () => {
    localStorage.setItem('portfolio_transactions', 'old')
    vi.mocked(api.deviceTransferRestore).mockResolvedValue({
      restore_point: 'rp9', categories: ['portfolio'], written: 0, removed: 0,
      browser_storage: { portfolio: { portfolio_transactions: 'new' } },
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {
      throw new Error('QuotaExceededError')
    })
    await expect(restoreBackup(OPTIONS, new File(['x'], 'b'), ['portfolio'])).rejects.toThrow('已復原')
    expect(api.deviceTransferRollback).toHaveBeenCalledWith('rp9')
    expect(localStorage.getItem('portfolio_transactions')).toBe('old')
    setItem.mockRestore()
  })
})
