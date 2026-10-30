// Generic personal Supabase login form - NEW, unlike the other components in
// this folder. The reference implementation (Mara) never had this as a
// standalone component: its password form was embedded directly inside
// Mara's own business dashboard page. Built fresh here, with no business
// logic, so it can be dropped into any employee regardless of what that
// employee's "Panel"/home page ends up being called.
//
// Only meaningful in rls_personal mode - in service_role mode there is no
// personal account to log into, so this renders an informational message
// instead of a form. Mount it wherever this employee's "reconnect" link
// (see SupabaseSessionBanner's `loginPath` prop) should point.
//
// ADJUST BEFORE USE: assumes MUI, `@tanstack/react-query`, `react-i18next`,
// and `useAuth()` exporting `{ email }` - confirm against the real template.
import { useState } from 'react'
import { Alert, Button, Card, CardContent, CircularProgress, Stack, TextField, Typography } from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/AuthContext'
import { requestSupabaseSessionRefresh, supabaseErrorCode, supabaseRequest } from '../services/supabaseApi'
import type { SupabaseSession } from '../services/supabaseApi'
import { useSupabaseSession } from '../hooks/useSupabaseSession'

export default function SupabaseLoginForm() {
  const { email } = useAuth()
  return email ? <LoginForm key={email} /> : null
}

function LoginForm() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const session = useSupabaseSession()
  const [password, setPassword] = useState('')

  const login = useMutation({
    mutationFn: async (value: string) => {
      try {
        return await supabaseRequest<{ connected: true; email: string }>('/session', {
          method: 'POST',
          body: JSON.stringify({ password: value }),
        })
      } finally {
        // Never let a submitted password linger in a retried mutation.
      }
    },
    retry: false,
    onSuccess: async () => {
      setPassword('')
      const refresh = queryClient.invalidateQueries({ queryKey: ['supabase-session'] }, { cancelRefetch: false })
      requestSupabaseSessionRefresh()
      await refresh
    },
    onSettled: () => { setPassword('') },
  })

  const logout = useMutation({
    mutationFn: () => supabaseRequest<{ connected: false }>('/session', { method: 'DELETE' }),
    retry: false,
    onSuccess: async () => {
      const refresh = queryClient.invalidateQueries({ queryKey: ['supabase-session'] }, { cancelRefetch: false })
      requestSupabaseSessionRefresh()
      await refresh
    },
  })

  if (session.isPending || (session.isFetching && session.data === undefined)) {
    return (
      <Stack direction="row" spacing={1} alignItems="center" role="status">
        <CircularProgress size={20} />
        <Typography variant="body2">{t('supabaseLogin.loading')}</Typography>
      </Stack>
    )
  }

  const data = session.data as SupabaseSession | undefined
  if (!data || data.auth_mode !== 'rls_personal') {
    return <Alert severity="info">{t('supabaseLogin.notApplicable')}</Alert>
  }
  if (data.state === 'not_configured') {
    return <Alert severity="info">{t('supabaseLogin.notConfigured')}</Alert>
  }

  if (session.ready) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            <Typography variant="body2">{t('supabaseLogin.connectedAs', { email: data.email })}</Typography>
            <Button variant="outlined" color="inherit" disabled={logout.isPending}
              onClick={() => logout.mutate()}>
              {t(logout.isPending ? 'supabaseLogin.loggingOut' : 'supabaseLogin.logout')}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={2} component="form" autoComplete="off" onSubmit={(event) => {
          event.preventDefault()
          const value = password
          if (!value || login.isPending) return
          login.mutate(value)
        }}>
          <Typography variant="body2">{t('supabaseLogin.prompt', { email: data.email })}</Typography>
          {login.isError && (
            <Alert severity="error">{t(`supabaseConfiguration.errors.${supabaseErrorCode(login.error)}`)}</Alert>
          )}
          <TextField
            fullWidth required type="password" autoComplete="current-password"
            label={t('supabaseLogin.passwordLabel')}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={login.isPending}
          />
          <Button type="submit" variant="contained" disabled={!password || login.isPending}>
            {t(login.isPending ? 'supabaseLogin.loggingIn' : 'supabaseLogin.login')}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  )
}
