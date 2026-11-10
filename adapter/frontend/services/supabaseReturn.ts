const KEY = 'sgmcp_adapter.supabase.return.v1'
const TTL_MS = 30 * 60 * 1000

type SupabaseReturn = {
  destination: string
  createdAt: number
  needsLogin: boolean
}

export function supabaseDestination(pathname: string, search = ''): string | null {
  if (pathname === '/supabase-login') return pathname
  if (pathname !== '/supabase-canal-verificar') return null
  const token = new URLSearchParams(search).get('token')
  if (!token || token.length > 8192 || /[\x00-\x1f\x7f]/.test(token)) return pathname
  return `${pathname}?${new URLSearchParams({ token })}`
}

export function getSupabaseReturn(): SupabaseReturn | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(KEY) || 'null')
    if (!value || typeof value.destination !== 'string'
      || !value.destination.startsWith('/') || value.destination.startsWith('//')
      || value.destination.includes('\\')
      || typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt)
      || Date.now() - value.createdAt < 0 || Date.now() - value.createdAt > TTL_MS
      || typeof value.needsLogin !== 'boolean') return null
    const url = new URL(value.destination, window.location.origin)
    if (url.origin !== window.location.origin || url.hash
      || supabaseDestination(url.pathname, url.search) !== value.destination) return null
    return value as SupabaseReturn
  } catch {
    return null
  }
}

export function rememberSupabaseReturn(destination: string, needsLogin: boolean) {
  try {
    const previous = getSupabaseReturn()
    window.sessionStorage.setItem(KEY, JSON.stringify({
      destination,
      createdAt: previous?.destination === destination ? previous.createdAt : Date.now(),
      needsLogin,
    }))
  } catch { /* Si el navegador bloquea storage, se puede volver a abrir el enlace. */ }
}

export function clearSupabaseReturn() {
  try { window.sessionStorage.removeItem(KEY) } catch { /* Storage opcional. */ }
}
