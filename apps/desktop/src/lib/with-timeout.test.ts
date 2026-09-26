import { describe, expect, it, vi } from 'vitest'

import { DEFAULT_BACKEND_READY_TIMEOUT_MS } from '../../electron/backend-health'
import { DEFAULT_PROBE_TIMEOUT_MS } from '../../electron/backend-probes'
import { DEFAULT_PORT_ANNOUNCE_TIMEOUT_MS } from '../../electron/backend-ready'
import { BACKEND_BOOT_WAIT_TIMEOUT_MS, TimeoutError, withTimeout } from './with-timeout'

describe('withTimeout', () => {
  it('allows the main-process default probe, announcement and readiness phases to finish', () => {
    expect(BACKEND_BOOT_WAIT_TIMEOUT_MS).toBeGreaterThan(
      DEFAULT_PROBE_TIMEOUT_MS + DEFAULT_PORT_ANNOUNCE_TIMEOUT_MS + DEFAULT_BACKEND_READY_TIMEOUT_MS
    )
  })

  it('keeps a healthy cold boot pending past 45 seconds and accepts its late descriptor', async () => {
    vi.useFakeTimers()
    try {
      let resolveBackend!: (value: string) => void
      const backend = new Promise<string>(resolve => { resolveBackend = resolve })
      const completed = vi.fn()
      const result = withTimeout(backend, BACKEND_BOOT_WAIT_TIMEOUT_MS, 'boot timed out')
      void result.then(completed)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(completed).not.toHaveBeenCalled()
      resolveBackend('ready')
      await expect(result).resolves.toBe('ready')
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('surfaces an explicit startup failure immediately without waiting for the cold boot budget', async () => {
    vi.useFakeTimers()
    try {
      const failure = new Error('backend process exited before READY')
      const result = withTimeout(Promise.reject(failure), BACKEND_BOOT_WAIT_TIMEOUT_MS, 'boot timed out')
      await expect(result).rejects.toBe(failure)
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('still bounds a backend request that never settles', async () => {
    vi.useFakeTimers()
    try {
      const result = withTimeout(new Promise<never>(() => undefined), BACKEND_BOOT_WAIT_TIMEOUT_MS, 'boot timed out')
      const rejection = expect(result).rejects.toBeInstanceOf(TimeoutError)
      await vi.advanceTimersByTimeAsync(BACKEND_BOOT_WAIT_TIMEOUT_MS)
      await rejection
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('rejects with an onTimeout exception instead of letting it escape the timer callback', async () => {
    vi.useFakeTimers()

    try {
      const callbackFailure = new Error('abort callback failed')

      const result = withTimeout(new Promise<never>(() => undefined), 10, 'work timed out', () => {
        throw callbackFailure
      })

      const rejection = expect(result).rejects.toBe(callbackFailure)

      await vi.advanceTimersByTimeAsync(10)
      await rejection
    } finally {
      vi.useRealTimers()
    }
  })
})
