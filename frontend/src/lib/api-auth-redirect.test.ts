import { describe, expect, it } from 'vitest'
import { ApiError, isAuthRedirectError } from './api'

describe('isAuthRedirectError', () => {
  it('401 一律視為未登入，不看文案', () => {
    expect(isAuthRedirectError(new ApiError('any text', 401))).toBe(true)
  })

  it('403 只有 NOT_INITIALIZED 才跳登入', () => {
    expect(isAuthRedirectError(new ApiError('x', 403, 'NOT_INITIALIZED'))).toBe(true)
    expect(isAuthRedirectError(new ApiError('x', 403))).toBe(false)
  })

  it('錯誤文案含 401 (如股票代號) 但狀態碼不是 401 不會誤判', () => {
    expect(isAuthRedirectError(new ApiError('找不到代號 2401', 404))).toBe(false)
    expect(isAuthRedirectError(new Error('401'))).toBe(false)
  })
})
