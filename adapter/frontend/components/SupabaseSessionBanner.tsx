// Generic session-status banner. Adapted from Mara's version, which was
// already business-logic-free except for one hardcoded detail: the "open
// panel" link pointed at Mara's own `/dashboard` route. That's now a prop -
// pass wherever SupabaseLoginForm actually lives for this employee.
//
// Mount this once, globally, inside the authenticated app shell (in Mara:
// inside <Layout>, above <Routes>) so it is visible from any page.
//
// ADJUST BEFORE USE: assumes MUI (`@mui/material`), `react-router-dom` and
// `react-i18next` are already the template's stack (service_role mode never
// shows a "reconnect" banner at all, since there is no personal session).
import { Alert, Button, Stack } from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useSupabaseSession } from '../hooks/useSupabaseSession'

export default function SupabaseSessionBanner({ loginPath }: { loginPath: string }) {
  const { t } = useTranslation()
  const session = useSupabaseSession()
  if (!session.state || session.state === 'connected') return null

  const severity = session.state === 'reauth_required' ? 'error'
    : session.state === 'unavailable' ? 'warning' : 'info'
  return (
    <Alert severity={severity} sx={{ mb: 2, flexShrink: 0 }}>
      <Stack spacing={1} alignItems="flex-start">
        <span>{t(`supabaseSession.${session.state}`)}</span>
        {session.reasonCode && <span>{t(`supabaseConfiguration.errors.${session.reasonCode}`)}</span>}
        {session.state === 'unavailable' ? (
          <Button size="small" color="inherit" disabled={session.isFetching || !session.identityValid}
            onClick={() => void session.refetch()}>{t('common.retry')}</Button>
        ) : session.state !== 'not_configured' && (
          <Button size="small" color="inherit" component={RouterLink} to={loginPath}>
            {t('supabaseSession.openPanel')}
          </Button>
        )}
      </Stack>
    </Alert>
  )
}
