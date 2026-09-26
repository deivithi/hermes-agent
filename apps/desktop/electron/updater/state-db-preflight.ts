import { execFile } from 'node:child_process'

import { hiddenWindowsChildOptions } from '../windows-child-options'

interface StateDbPreflight {
  python: string | null
  script: string
  home: string
  log: (message: string) => void
  timeoutMs?: number
}

export const STATE_DB_PREFLIGHT_TIMEOUT_MS = 10 * 60_000

// Await the snapshot before shutdown, without blocking Electron's main loop.
export async function preflightStateDb({
  python,
  script,
  home,
  log,
  timeoutMs = STATE_DB_PREFLIGHT_TIMEOUT_MS
}: StateDbPreflight): Promise<void> {
  try {
    if (!python) {
      throw new Error('Python not found')
    }

    log(`[updates] state.db pre-flight: snapshot and integrity check started (limit ${timeoutMs / 1000}s)`)
    const result = await new Promise<string>((resolve, reject) => {
      execFile(
        python,
        ['-I', '-S', script, home],
        hiddenWindowsChildOptions({ encoding: 'utf8', timeout: timeoutMs }),
        (error, stdout) => {
          if (error) {
            reject(
              error.killed
                ? new Error(`Snapshot and integrity check exceeded ${timeoutMs / 1000} seconds`, { cause: error })
                : error
            )
          } else {
            resolve(stdout)
          }
        }
      )
    })

    log(`[updates] state.db pre-flight: ${result.trim()}`)
  } catch (error: unknown) {
    const message =
      `state.db pre-flight failed: ${error instanceof Error ? error.message : String(error)}. ` +
      'Update cancelled before backend shutdown. Update the selected installation with its hermes update command, then retry.'

    log(`[updates] ${message}`)
    throw new Error(message, { cause: error })
  }
}
