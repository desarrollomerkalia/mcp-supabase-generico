// Generic react-query hook over GET /api/supabase/session. Reused verbatim
// from a working implementation (Mara) - this file has no business logic in
// it, so unlike supabaseApi.ts it needed no changes beyond the import path.
//
// ADJUST BEFORE USE: assumes '../auth/AuthContext' exports a `useAuth()`
// hook returning at least `{ email: string | null }` - confirm against the
// real template.
import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthContext'
import {
  SUPABASE_SESSION_INVALIDATED, SUPABASE_SESSION_REFRESH,
  SupabaseError, supabaseErrorCode, supabaseRequest,
} from '../services/supabaseApi'
import type { SupabaseSession, SupabaseSessionState } from '../services/supabaseApi'

// Keep session/configuration metadata; purge only Supabase business-data queries.
const dataQueries = { predicate: (query: { queryKey: readonly unknown[] }) =>
  typeof query.queryKey[0] === 'string' && query.queryKey[0].startsWith('supabase-')
  && query.queryKey[0] !== 'supabase-session' && query.queryKey[0] !== 'supabase-configuration' }

export function useSupabaseSession() {
  const { email } = useAuth()
  const queryClient = useQueryClient()
  const identityValid = typeof email === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
  const queryKey = ['supabase-session', email] as const
  const session = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      try {
        const next = await supabaseRequest<SupabaseSession>('/session', { signal })
        if (next.email.trim().toLowerCase() !== email?.trim().toLowerCase()) {
          throw new SupabaseError('supabase_identity_mismatch')
        }
        const previous = queryClient.getQueryData<SupabaseSession>(queryKey)
        if (next.state !== 'connected' || previous?.project_url !== next.project_url) {
          await queryClient.cancelQueries(dataQueries)
          queryClient.removeQueries(dataQueries)
        }
        return next
      } catch (error) {
        if (!signal.aborted) {
          await queryClient.cancelQueries(dataQueries)
          queryClient.removeQueries(dataQueries)
        }
        throw error
      }
    },
    enabled: identityValid,
    placeholderData: undefined,
    gcTime: 0,
    staleTime: 15_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: 'always',
    retry: false,
  })

  useEffect(() => {
    if (!identityValid) return
    const refresh = () => {
      // Multiple observers share one request; never cancel/restart one another.
      void queryClient.refetchQueries({ queryKey: ['supabase-session', email], exact: true }, { cancelRefetch: false })
    }
    const invalidate = () => {
      void queryClient.cancelQueries(dataQueries)
      queryClient.removeQueries(dataQueries)
      refresh()
    }
    window.addEventListener(SUPABASE_SESSION_INVALIDATED, invalidate)
    window.addEventListener(SUPABASE_SESSION_REFRESH, refresh)
    return () => {
      window.removeEventListener(SUPABASE_SESSION_INVALIDATED, invalidate)
      window.removeEventListener(SUPABASE_SESSION_REFRESH, refresh)
    }
  }, [email, identityValid, queryClient])

  useEffect(() => () => {
    queueMicrotask(() => {
      const query = queryClient.getQueryCache().find({ queryKey: ['supabase-session', email], exact: true })
      if (!query?.getObserversCount()) {
        void queryClient.cancelQueries(dataQueries)
        queryClient.removeQueries(dataQueries)
      }
    })
  }, [email, queryClient])

  const identityMismatch = session.data !== undefined
    && session.data.email.trim().toLowerCase() !== email?.trim().toLowerCase()
  const errorCode = supabaseErrorCode(session.error)
  const authRejected = ['supabase_session_expired', 'supabase_access_token_invalid',
    'supabase_session_required', 'supabase_auth_rejected'].includes(errorCode)
  const state: SupabaseSessionState | null = !identityValid || identityMismatch ? 'unavailable'
    : session.isError ? (authRejected ? 'reauth_required' : 'unavailable') : session.data?.state ?? null
  const reasonCode = !identityValid || identityMismatch ? 'supabase_identity_mismatch'
    : session.isError ? errorCode : session.data?.reason_code ?? null
  return { ...session, state, reasonCode, ready: state === 'connected', identityValid }
}
