// Verification page for a WhatsApp (or other channel) magic link - NOT the
// same thing as SupabaseLoginForm.tsx. That one is for the web chat's own
// personal session. This one consumes a short-lived token (the ?token=...
// query param) sent to the user via the channel itself, and on top of doing
// the exact same real Supabase login, also links that channel identity to
// the now-authenticated email - see channel_binding.py/routes.py's
// POST /api/supabase/channel-session for the actual verification logic;
// this component never decides identity on its own.
//
// Mount at a dedicated route, e.g. /supabase-canal-verificar - the link
// tools.py generates points there with ?token=<...>. A visitor with no
// token (or an expired one) still gets to log in normally; they are told
// the channel link itself didn't bind, not that login failed.
//
// ADJUST BEFORE USE: assumes MUI, `@tanstack/react-query`, `react-i18next`,
// `useAuth()` exporting `{ email }`, and a router hook/API to read the
// current URL's query string - confirm against the real template.
import { useState } from 'react'
import { Alert, Button, Card, CardContent, Stack, TextField, Typography } from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/AuthContext'
import { supabaseErrorCode, supabaseRequest } from '../services/supabaseApi'
import type { SupabaseChannelSessionResult } from '../services/supabaseApi'

export default function SupabaseChannelVerifyForm() {
  const { email } = useAuth()
  const token = new URLSearchParams(window.location.search).get('token') ?? ''

  return email ? <VerifyForm key={email} token={token} /> : null
}

function VerifyForm({ token }: { token: string }) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')

  const verify = useMutation({
    mutationFn: (value: string) => {
      return supabaseRequest<SupabaseChannelSessionResult>('/channel-session', {
        method: 'POST',
        body: JSON.stringify({ token, password: value }),
      })
    },
    retry: false,
    onSettled: () => { setPassword('') },
  })

  if (!token) {
    return <Alert severity="error">{t('supabaseChannelVerify.missingToken')}</Alert>
  }

  if (verify.isSuccess) {
    const result = verify.data
    return (
      <Card variant="outlined">
        <CardContent>
          <Alert severity={result.channel_bound ? 'success' : 'warning'}>
            {t(result.channel_bound ? 'supabaseChannelVerify.bound' : 'supabaseChannelVerify.loginOkBindFailed', { email: result.email })}
          </Alert>
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
          if (!value || verify.isPending) return
          verify.mutate(value)
        }}>
          <Typography variant="body2">{t('supabaseChannelVerify.prompt')}</Typography>
          {verify.isError && (
            <Alert severity="error">{t(`supabaseConfiguration.errors.${supabaseErrorCode(verify.error)}`)}</Alert>
          )}
          <TextField
            fullWidth required type="password" autoComplete="current-password"
            label={t('supabaseChannelVerify.passwordLabel')}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={verify.isPending}
          />
          <Button type="submit" variant="contained" disabled={!password || verify.isPending}>
            {t(verify.isPending ? 'supabaseChannelVerify.verifying' : 'supabaseChannelVerify.verify')}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  )
}
