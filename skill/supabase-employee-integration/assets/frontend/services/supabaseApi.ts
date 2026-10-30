// Generic REST client for sgmcp_adapter's /api/supabase/* routes.
//
// Adapted from a working, verified implementation (an employee called Mara)
// that only ever used rls_personal mode and never exposed a table/column
// allowlist in its UI - this version adds auth_mode, service_role_key and
// the tables/denied/read_limit fields sgmcp_adapter's ConnectionConfig
// needs, while keeping Mara's defensive response-shape validation intact.
//
// Verified against the real template (wai-agent-app-builder 12.59.1):
// `apiFetch(input: string, init?: RequestInit): Promise<Response>` lives at
// `src/frontend/src/services/apiClient.ts`, takes the FULL path (it does not
// prepend /api itself - callers below already pass '/api/supabase...'),
// attaches the WAI JWT, and returns a Response for ordinary HTTP errors
// rather than throwing (hence checking `response.ok` / `response.status`
// below, not just catching). The outer try/catch here only exists for
// genuine transport failures (DNS, connection refused) and for hosts whose
// apiFetch *does* throw Error('Unauthorized') on session loss - harmless to
// keep even if this template's apiFetch never throws that way.
import { apiFetch } from './apiClient'

export const SUPABASE_ERROR_CODES = [
  'sgmcp_read_rejected',
  'sgmcp_read_response_invalid',
  'sgmcp_read_unavailable',
  'supabase_access_token_invalid',
  'supabase_anon_key_missing',
  'supabase_api_key_rejected',
  'supabase_auth_mode_mismatch',
  'supabase_auth_rejected',
  'supabase_configuration_invalid',
  'supabase_configuration_storage_unavailable',
  'supabase_configuration_unreadable',
  'supabase_configuration_validation_unavailable',
  'supabase_email_not_confirmed',
  // Frontend-synthesized, not sent by the backend: wrong content-type, 404/405.
  'supabase_endpoint_unavailable',
  'supabase_identity_mismatch',
  'supabase_invalid_credentials',
  'supabase_invalid_response',
  'supabase_not_configured',
  'supabase_operation_failed',
  'supabase_rate_limited',
  'supabase_request_invalid',
  'supabase_service_role_key_missing',
  'supabase_session_expired',
  'supabase_session_required',
  'supabase_session_unavailable',
  'supabase_tables_required',
  'supabase_unavailable',
  'supabase_write_not_sent',
  'wai_permission_denied',
  // Frontend-synthesized: the WAI session itself (not the Supabase one) is gone.
  'wai_session_expired',
] as const

export type SupabaseErrorCode = typeof SUPABASE_ERROR_CODES[number]

export const SUPABASE_AUTH_MODES = ['rls_personal', 'service_role'] as const
export type SupabaseAuthMode = typeof SUPABASE_AUTH_MODES[number]

export const SUPABASE_SESSION_STATES = [
  'connected', 'disconnected', 'reauth_required', 'unavailable', 'not_configured',
] as const
export type SupabaseSessionState = typeof SUPABASE_SESSION_STATES[number]

// Notification only: never put identity, credentials or response values in events.
export const SUPABASE_SESSION_INVALIDATED = 'supabase-session-invalidated'
export const SUPABASE_SESSION_REFRESH = 'supabase-session-refresh'
export function requestSupabaseSessionRefresh() {
  window.dispatchEvent(new Event(SUPABASE_SESSION_REFRESH))
}

export type SupabaseSession = {
  connected: boolean
  email: string
  configured: boolean
  configuration_code: SupabaseErrorCode | null
  project_url: string | null
  auth_mode: SupabaseAuthMode
  can_configure: boolean
  state: SupabaseSessionState
  reason_code: SupabaseErrorCode | null
  expires_at: number | null
  last_verified_at: number | null
}

/** A table's allowlist entry, as sgmcp_adapter.config.TableConfig.to_dict(). */
export type SupabaseTableAllowlist = {
  columns: Record<string, {
    type: 'string' | 'integer' | 'number' | 'boolean'
    enum: string[] | null
    pattern: string | null
    min_length: number | null
    max_length: number | null
    required: boolean
  }>
  writable: string[]
  filterable: string[]
}

export type SupabaseConfiguration = Pick<
  SupabaseSession, 'configured' | 'configuration_code' | 'project_url' | 'auth_mode' | 'can_configure'
> & {
  has_anon_key: boolean
  has_service_role_key: boolean
  tables: Record<string, SupabaseTableAllowlist>
  denied: string[]
  read_limit: number
}

export type SupabaseConfigurationInput = {
  project_url: string
  auth_mode: SupabaseAuthMode
  anon_key?: string
  service_role_key?: string
  tables: Record<string, SupabaseTableAllowlist>
  denied: string[]
  read_limit: number
}

export type SupabaseConfigurationResult = {
  configured: true
  changed: boolean
  project_url: string
  auth_mode: SupabaseAuthMode
  has_anon_key: boolean
  has_service_role_key: boolean
}

export function knownSupabaseCode(value: unknown): SupabaseErrorCode {
  return typeof value === 'string' && SUPABASE_ERROR_CODES.some((code) => code === value)
    ? value as SupabaseErrorCode
    : 'supabase_operation_failed'
}

export class SupabaseError extends Error {
  readonly code: SupabaseErrorCode

  constructor(code: SupabaseErrorCode) {
    const safeCode = knownSupabaseCode(code)
    super(safeCode)
    this.name = 'SupabaseError'
    this.code = safeCode
  }
}

export function supabaseErrorCode(error: unknown): SupabaseErrorCode {
  return error instanceof SupabaseError ? knownSupabaseCode(error.code) : 'supabase_operation_failed'
}

export async function supabaseRequest<T>(
  path: '/session' | '/configuration',
  init: RequestInit = {},
): Promise<T> {
  let response: Response
  try {
    // The backend maps Supabase session failures to 409/422, never WAI's 401.
    response = await apiFetch(`/api/supabase${path}`, init)
  } catch (error) {
    // Keep transport/quarantine failures safe; never propagate raw errors.
    // This template's apiFetch returns a Response rather than throwing on
    // ordinary auth failures (see response.ok handling below) - this catch
    // is a safety net for transport-level failures only.
    const code = error instanceof Error && error.message === 'Unauthorized'
      ? 'wai_session_expired'
      : 'supabase_unavailable'
    throw new SupabaseError(code)
  }

  // Configuration access is admin-scoped; never infer it from client-side roles.
  if (path === '/configuration' && (init.method ?? 'GET').toUpperCase() === 'GET'
    && response.status === 403) return null as T

  if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
    throw new SupabaseError('supabase_endpoint_unavailable')
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new SupabaseError('supabase_invalid_response')
  }

  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    const code = detail && typeof detail === 'object' && 'code' in detail ? detail.code : null
    const fallback: SupabaseErrorCode = response.status === 401 ? 'wai_session_expired'
      : response.status === 403 ? 'wai_permission_denied'
        : response.status === 404 || response.status === 405 ? 'supabase_endpoint_unavailable'
          : response.status === 429 ? 'supabase_rate_limited'
            : [502, 503, 504].includes(response.status) ? 'supabase_unavailable'
              : response.status === 409 ? 'supabase_session_required'
                : response.status === 422 ? 'supabase_request_invalid'
                  : 'supabase_operation_failed'
    const safeCode = code === null ? fallback : knownSupabaseCode(code)
    if (!(path === '/session' && (init.method ?? 'GET').toUpperCase() === 'GET')
      && ['supabase_session_expired', 'supabase_access_token_invalid',
        'supabase_session_required', 'supabase_auth_rejected', 'supabase_identity_mismatch',
        'supabase_session_unavailable'].includes(safeCode)) {
      window.dispatchEvent(new Event(SUPABASE_SESSION_INVALIDATED))
    }
    throw new SupabaseError(safeCode)
  }

  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new SupabaseError('supabase_invalid_response')
  }

  const isValidAuthMode = (value: unknown): value is SupabaseAuthMode =>
    typeof value === 'string' && SUPABASE_AUTH_MODES.some((mode) => mode === value)

  if ((path === '/session' || path === '/configuration') && (init.method ?? 'GET').toUpperCase() === 'GET') {
    if (!('configured' in payload) || typeof payload.configured !== 'boolean'
      || !('configuration_code' in payload)
      || (payload.configuration_code !== null
        && !SUPABASE_ERROR_CODES.some((code) => code === payload.configuration_code))
      || !('project_url' in payload) || (payload.project_url !== null && typeof payload.project_url !== 'string')
      || !('auth_mode' in payload) || !isValidAuthMode(payload.auth_mode)
      || !('can_configure' in payload) || typeof payload.can_configure !== 'boolean') {
      throw new SupabaseError('supabase_invalid_response')
    }
    const configuration = {
      configured: payload.configured,
      configuration_code: payload.configuration_code === null ? null : knownSupabaseCode(payload.configuration_code),
      project_url: payload.project_url,
      auth_mode: payload.auth_mode,
      can_configure: payload.can_configure,
    }
    if (path === '/configuration') {
      if (!('has_anon_key' in payload) || typeof payload.has_anon_key !== 'boolean'
        || !('has_service_role_key' in payload) || typeof payload.has_service_role_key !== 'boolean'
        || !('tables' in payload) || payload.tables === null || typeof payload.tables !== 'object'
        || !('denied' in payload) || !Array.isArray(payload.denied)
        || !('read_limit' in payload) || typeof payload.read_limit !== 'number') {
        throw new SupabaseError('supabase_invalid_response')
      }
      return {
        ...configuration,
        has_anon_key: payload.has_anon_key,
        has_service_role_key: payload.has_service_role_key,
        tables: payload.tables as Record<string, SupabaseTableAllowlist>,
        denied: payload.denied as string[],
        read_limit: payload.read_limit,
      } as T
    }
    if (!('connected' in payload) || typeof payload.connected !== 'boolean'
      || !('email' in payload) || typeof payload.email !== 'string'
      || !('state' in payload) || !SUPABASE_SESSION_STATES.some((state) => state === payload.state)
      || !('reason_code' in payload) || (payload.reason_code !== null
        && !SUPABASE_ERROR_CODES.some((code) => code === payload.reason_code))
      || !('expires_at' in payload) || !('last_verified_at' in payload)
      || ![payload.expires_at, payload.last_verified_at].every((value) => value === null
        || (typeof value === 'number' && Number.isFinite(value) && value >= 0))
      || payload.connected !== (payload.state === 'connected')
      || (payload.state === 'connected' && (!payload.configured || payload.reason_code !== null))
      || (payload.state === 'not_configured' && payload.configured)
      || (!payload.configured && payload.state !== 'not_configured' && payload.state !== 'unavailable')) {
      throw new SupabaseError('supabase_invalid_response')
    }
    // Neither query retains keys, ciphertext, passwords or tokens from a response.
    return {
      connected: payload.connected, email: payload.email, ...configuration,
      state: payload.state, reason_code: payload.reason_code,
      expires_at: payload.expires_at, last_verified_at: payload.last_verified_at,
    } as T
  }
  if (path === '/configuration' && (init.method ?? 'GET').toUpperCase() === 'PUT') {
    if (!('configured' in payload) || payload.configured !== true
      || !('changed' in payload) || typeof payload.changed !== 'boolean'
      || !('project_url' in payload) || typeof payload.project_url !== 'string'
      || !('auth_mode' in payload) || !isValidAuthMode(payload.auth_mode)
      || !('has_anon_key' in payload) || typeof payload.has_anon_key !== 'boolean'
      || !('has_service_role_key' in payload) || typeof payload.has_service_role_key !== 'boolean') {
      throw new SupabaseError('supabase_invalid_response')
    }
    return {
      configured: true, changed: payload.changed,
      project_url: payload.project_url, auth_mode: payload.auth_mode,
      has_anon_key: payload.has_anon_key, has_service_role_key: payload.has_service_role_key,
    } as T
  }
  return payload as T
}
