import { BACKEND_BOOT_WAIT_TIMEOUT_MS as READINESS_TIMEOUT_MS } from './with-timeout'

// Main awaits the runtime probe, port announcement, and readiness sequentially.
// Keep the official readiness constant untouched so upstream updates to it are
// inherited without a conflicting local edit. Electron phase constants are
// checked by the contract test, never imported into the renderer bundle.
export const BACKEND_BOOT_WAIT_TIMEOUT_MS = 15_000 + 90_000 + READINESS_TIMEOUT_MS + 5_000
