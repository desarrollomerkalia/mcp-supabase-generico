// Generic admin configuration screen for a Supabase-connected employee.
// Extends a working, verified implementation (Mara's SupabaseProjectConfiguration)
// that never needed to configure tables/columns (its allowlist was hardcoded
// in Python) and never supported service_role mode. Both are new here.
//
// The table/column allowlist used to be a raw JSON text area (an admin
// pasted a JSON blob the building agent proposed). Changed on purpose:
// business builders using this skill are explicitly non-technical, and
// "paste this JSON correctly" was the single biggest complaint about the
// whole integration. This is now a structured, repeatable table/column
// builder - no JSON syntax ever shown to the admin. It still serializes
// to the exact same wire shape sgmcp_adapter.config.ConnectionConfig
// expects (see buildAllowlistPayload below), so the backend contract did
// not change at all - only this component did.
//
// Mount this inside the employee's existing admin "Conectores" section (in
// Mara: Operations.tsx, as a sibling placed before the generic Composio
// connectors list - NOT inside that generic mechanism, Supabase has its own
// backend).
//
// ADJUST BEFORE USE: assumes MUI, `@tanstack/react-query`, `react-i18next`,
// and a `useAuth()` hook exporting `{ email }` - confirm against the real
// template.
import { useRef, useState } from 'react'
import {
  Alert, Button, Card, CardContent, Checkbox, CircularProgress, Collapse, Divider, FormControlLabel,
  MenuItem, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/AuthContext'
import {
  knownSupabaseCode, requestSupabaseSessionRefresh, SUPABASE_AUTH_MODES, SupabaseError,
  supabaseErrorCode, supabaseRequest,
} from '../services/supabaseApi'
import type {
  SupabaseAuthMode, SupabaseConfiguration, SupabaseConfigurationInput,
  SupabaseConfigurationResult, SupabaseErrorCode, SupabaseTableAllowlist,
} from '../services/supabaseApi'

type ColumnType = 'string' | 'integer' | 'number' | 'boolean'
const COLUMN_TYPES: ColumnType[] = ['string', 'integer', 'number', 'boolean']

type ColumnDraft = {
  id: string
  name: string
  type: ColumnType
  required: boolean
  writable: boolean
  filterable: boolean
  enumText: string
  pattern: string
  minLength: string
  maxLength: string
  advancedOpen: boolean
}

type TableDraft = {
  id: string
  name: string
  columns: ColumnDraft[]
  collapsed: boolean
}

let draftIdCounter = 0
function nextDraftId(): string {
  draftIdCounter += 1
  return 'd' + String(draftIdCounter)
}

function emptyColumn(): ColumnDraft {
  return {
    id: nextDraftId(), name: '', type: 'string', required: false, writable: true, filterable: true,
    enumText: '', pattern: '', minLength: '', maxLength: '', advancedOpen: false,
  }
}

function emptyTable(): TableDraft {
  // Starts expanded: an admin who just clicked "add table" is actively
  // filling it in, so there is nothing to collapse yet.
  return { id: nextDraftId(), name: '', columns: [emptyColumn()], collapsed: false }
}

function tablesFromConfig(tables: Record<string, SupabaseTableAllowlist>): TableDraft[] {
  // Starts collapsed: a saved configuration can have several tables, and
  // showing all of them fully expanded on load would be a wall of fields
  // before the admin has touched anything.
  return Object.entries(tables).map(([tableName, table]) => ({
    id: nextDraftId(),
    name: tableName,
    collapsed: true,
    columns: Object.entries(table.columns).map(([columnName, rule]) => ({
      id: nextDraftId(),
      name: columnName,
      type: rule.type,
      required: rule.required,
      writable: table.writable.includes(columnName),
      filterable: table.filterable.includes(columnName),
      enumText: rule.enum ? rule.enum.join(', ') : '',
      pattern: rule.pattern ?? '',
      minLength: rule.min_length != null ? String(rule.min_length) : '',
      maxLength: rule.max_length != null ? String(rule.max_length) : '',
      advancedOpen: false,
    })),
  }))
}

/** Mirrors sgmcp_adapter.config.ConnectionConfig.tables exactly. Returns
 * null when the draft is not yet a valid allowlist (empty names, duplicate
 * names, an invalid regex, an empty enum list) - callers use that to keep
 * the save button disabled rather than sending a request the backend would
 * reject anyway. */
function buildTablesPayload(tables: TableDraft[]): Record<string, SupabaseTableAllowlist> | null {
  if (tables.length === 0) return null
  const result: Record<string, SupabaseTableAllowlist> = {}
  for (const table of tables) {
    const tableName = table.name.trim()
    if (!tableName || tableName in result) return null
    if (table.columns.length === 0) return null
    const columns: SupabaseTableAllowlist['columns'] = {}
    const writable: string[] = []
    const filterable: string[] = []
    for (const column of table.columns) {
      const columnName = column.name.trim()
      if (!columnName || columnName in columns) return null
      const minLength = column.minLength.trim() ? Number(column.minLength.trim()) : null
      const maxLength = column.maxLength.trim() ? Number(column.maxLength.trim()) : null
      if ((minLength !== null && (!Number.isInteger(minLength) || minLength < 0))
        || (maxLength !== null && (!Number.isInteger(maxLength) || maxLength < 0))) return null
      const enumValues = column.enumText.trim()
        ? column.enumText.split(',').map((value) => value.trim()).filter(Boolean)
        : null
      if (enumValues !== null && enumValues.length === 0) return null
      if (column.pattern.trim()) {
        try { new RegExp(column.pattern.trim()) } catch { return null }
      }
      columns[columnName] = {
        type: column.type, enum: enumValues, pattern: column.pattern.trim() || null,
        min_length: minLength, max_length: maxLength, required: column.required,
      }
      if (column.writable) writable.push(columnName)
      if (column.filterable) filterable.push(columnName)
    }
    result[tableName] = { columns, writable, filterable }
  }
  return result
}

export default function SupabaseConfigurationForm() {
  const { email } = useAuth()
  return email ? <ConfigurationForm key={email} email={email} /> : null
}

function ConfigurationForm({ email }: { email: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const pendingConfiguration = useRef<SupabaseConfigurationInput | null>(null)
  const [projectUrlDraft, setProjectUrlDraft] = useState<string | null>(null)
  const [authModeDraft, setAuthModeDraft] = useState<SupabaseAuthMode | null>(null)
  const [keyDraft, setKeyDraft] = useState('')
  const [tablesDraft, setTablesDraft] = useState<TableDraft[] | null>(null)
  const [denyInsertDraft, setDenyInsertDraft] = useState<boolean | null>(null)
  const [denyUpdateDraft, setDenyUpdateDraft] = useState<boolean | null>(null)
  const [denyDeleteDraft, setDenyDeleteDraft] = useState<boolean | null>(null)
  const [readLimitDraft, setReadLimitDraft] = useState<string | null>(null)
  const [error, setError] = useState<SupabaseErrorCode | null>(null)
  const [saved, setSaved] = useState<'changed' | 'unchanged' | null>(null)
  const configuration = useQuery({
    queryKey: ['supabase-configuration', email],
    queryFn: ({ signal }) => supabaseRequest<SupabaseConfiguration | null>('/configuration', { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })
  const save = useMutation({
    mutationFn: async () => {
      const input = pendingConfiguration.current
      pendingConfiguration.current = null
      if (!input) throw new SupabaseError('supabase_request_invalid')
      try {
        const body = JSON.stringify(input)
        delete input.anon_key
        delete input.service_role_key
        return await supabaseRequest<SupabaseConfigurationResult>('/configuration', {
          method: 'PUT',
          body,
        })
      } finally {
        // Credentials never become mutation variables or cached response data.
        delete input.anon_key
        delete input.service_role_key
      }
    },
    retry: false,
    gcTime: 0,
    onMutate: () => { setError(null); setSaved(null) },
    onSuccess: async (result) => {
      if (result.changed) {
        const dataQueries = {
          predicate: (query: { queryKey: readonly unknown[] }) =>
            typeof query.queryKey[0] === 'string' && query.queryKey[0].startsWith('supabase-')
            && query.queryKey[0] !== 'supabase-session' && query.queryKey[0] !== 'supabase-configuration',
        }
        await queryClient.cancelQueries(dataQueries)
        queryClient.removeQueries(dataQueries)
        const refresh = queryClient.invalidateQueries({ queryKey: ['supabase-session'] }, { cancelRefetch: false })
        requestSupabaseSessionRefresh()
        await refresh
      }
      await queryClient.invalidateQueries({ queryKey: ['supabase-configuration'] })
      setProjectUrlDraft(null)
      setAuthModeDraft(null)
      setTablesDraft(null)
      setDenyInsertDraft(null)
      setDenyUpdateDraft(null)
      setDenyDeleteDraft(null)
      setReadLimitDraft(null)
      setSaved(result.changed ? 'changed' : 'unchanged')
    },
    onError: (failure) => setError(supabaseErrorCode(failure)),
    onSettled: () => { pendingConfiguration.current = null; setKeyDraft(''); save.reset() },
  })

  if ((configuration.isSuccess && configuration.data === null)
    || (configuration.isError && supabaseErrorCode(configuration.error) === 'wai_permission_denied')
    || error === 'wai_permission_denied') return null

  const data = configuration.data
  const projectUrl = projectUrlDraft ?? data?.project_url ?? ''
  const authMode = authModeDraft ?? data?.auth_mode ?? 'rls_personal'
  const tables = tablesDraft ?? (data ? tablesFromConfig(data.tables) : [])
  const denyInsert = denyInsertDraft ?? (data?.denied.includes('insertar_registros') ?? false)
  const denyUpdate = denyUpdateDraft ?? (data?.denied.includes('actualizar_registros') ?? false)
  const denyDelete = denyDeleteDraft ?? (data?.denied.includes('eliminar_registros') ?? false)
  const readLimitText = readLimitDraft ?? String(data?.read_limit ?? 10000)
  const tablesPayload = buildTablesPayload(tables)
  const readLimitNumber = /^[0-9]+$/.test(readLimitText.trim()) ? Number(readLimitText.trim()) : null
  const readLimitValid = readLimitNumber !== null && readLimitNumber >= 1 && readLimitNumber <= 10000
  const allowlistValid = tablesPayload !== null && readLimitValid
  const hasKeyForMode = authMode === 'rls_personal' ? data?.has_anon_key : data?.has_service_role_key
  // A pending or failed authorization check must never leave an editable form visible.
  const verified = configuration.isSuccess && !configuration.isFetching && !!data

  const touchTables = (next: TableDraft[]) => { setTablesDraft(next); setSaved(null) }
  const updateColumn = (tableId: string, columnId: string, patch: Partial<ColumnDraft>) => {
    touchTables(tables.map((table) => table.id !== tableId ? table : {
      ...table,
      columns: table.columns.map((column) => column.id !== columnId ? column : { ...column, ...patch }),
    }))
  }

  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6" sx={{ mb: 1 }}>{t('supabaseConfiguration.title')}</Typography>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">{t('supabaseConfiguration.description')}</Typography>
          {configuration.isPending || configuration.isFetching ? (
            <Stack direction="row" spacing={1} alignItems="center" role="status">
              <CircularProgress size={20} />
              <Typography variant="body2">{t('supabaseConfiguration.loading')}</Typography>
            </Stack>
          ) : !verified ? (
            <>
              <Alert severity="error">
                {t('supabaseConfiguration.loadError')} {t(`supabaseConfiguration.errors.${supabaseErrorCode(configuration.error)}`)}
              </Alert>
              <Button variant="outlined" onClick={() => void configuration.refetch()}>
                {t('supabaseConfiguration.retry')}
              </Button>
            </>
          ) : (
            <>
              <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
                {t('supabaseConfiguration.projectUrl', { url: data.project_url ?? t('supabaseConfiguration.projectUrlMissing') })}
              </Typography>
              <Alert severity={hasKeyForMode ? 'info' : 'warning'}>
                {t(hasKeyForMode ? 'supabaseConfiguration.keySaved' : 'supabaseConfiguration.notConfigured', { mode: t(`supabaseConfiguration.authMode.${authMode}`) })}
              </Alert>
              <Typography variant="body2" color="text.secondary">{t('supabaseConfiguration.statusHint')}</Typography>
              {data.configuration_code && (
                <Alert severity="warning">{t(`supabaseConfiguration.errors.${knownSupabaseCode(data.configuration_code)}`)}</Alert>
              )}
              {error && <Alert severity="error">{t(`supabaseConfiguration.errors.${error}`)}</Alert>}
              {saved && <Alert severity="success">{t(saved === 'changed' ? 'supabaseConfiguration.saved' : 'supabaseConfiguration.unchanged')}</Alert>}
              {data.can_configure === true ? (
                <Stack spacing={2} component="form" autoComplete="off" onInvalid={() => setKeyDraft('')} onSubmit={(event) => {
                  event.preventDefault()
                  const key = keyDraft.trim()
                  setKeyDraft('')
                  if (!verified || data.can_configure !== true || !projectUrl.trim() || !tablesPayload || !readLimitValid
                    || (!hasKeyForMode && !key) || save.isPending || pendingConfiguration.current) return
                  pendingConfiguration.current = {
                    project_url: projectUrl.trim(),
                    auth_mode: authMode,
                    ...(key ? (authMode === 'rls_personal' ? { anon_key: key } : { service_role_key: key }) : {}),
                    tables: tablesPayload,
                    denied: [
                      ...(denyInsert ? ['insertar_registros'] : []),
                      ...(denyUpdate ? ['actualizar_registros'] : []),
                      ...(denyDelete ? ['eliminar_registros'] : []),
                    ],
                    read_limit: readLimitNumber as number,
                  }
                  save.mutate()
                }}>
                  <Alert severity="warning">{t('supabaseConfiguration.sessionWarning')}</Alert>
                  <TextField
                    fullWidth required type="url" autoComplete="off"
                    label={t('supabaseConfiguration.projectUrlLabel')}
                    placeholder={t('supabaseConfiguration.projectUrlPlaceholder')}
                    value={projectUrl}
                    onFocus={() => setProjectUrlDraft(projectUrl)}
                    onChange={(event) => { setProjectUrlDraft(event.target.value); setSaved(null) }}
                    disabled={save.isPending}
                  />
                  <TextField
                    fullWidth select
                    label={t('supabaseConfiguration.authModeLabel')}
                    value={authMode}
                    onChange={(event) => { setAuthModeDraft(event.target.value as SupabaseAuthMode); setKeyDraft(''); setSaved(null) }}
                    disabled={save.isPending}
                    helperText={t(`supabaseConfiguration.authModeHelp.${authMode}`)}
                  >
                    {SUPABASE_AUTH_MODES.map((mode) => (
                      <MenuItem key={mode} value={mode}>{t(`supabaseConfiguration.authMode.${mode}`)}</MenuItem>
                    ))}
                  </TextField>
                  <Typography variant="body2" color="text.secondary">{t('supabaseConfiguration.keyHelp')}</Typography>
                  <TextField
                    fullWidth required={!hasKeyForMode} type="password" autoComplete="new-password"
                    label={t(authMode === 'rls_personal' ? 'supabaseConfiguration.anonKey' : 'supabaseConfiguration.serviceRoleKey')}
                    value={keyDraft}
                    helperText={t(hasKeyForMode ? 'supabaseConfiguration.keepKeyHint' : 'supabaseConfiguration.keyRequiredHint')}
                    onChange={(event) => { setKeyDraft(event.target.value); setSaved(null) }}
                    disabled={save.isPending}
                  />

                  <Divider />
                  <Typography variant="subtitle2">{t('supabaseConfiguration.operationsTitle')}</Typography>
                  <Typography variant="body2" color="text.secondary">{t('supabaseConfiguration.operationsHelp')}</Typography>
                  <Stack direction="row" spacing={2} flexWrap="wrap">
                    <FormControlLabel
                      control={<Checkbox checked={!denyInsert} disabled={save.isPending}
                        onChange={(event) => { setDenyInsertDraft(!event.target.checked); setSaved(null) }} />}
                      label={t('supabaseConfiguration.allowInsert')}
                    />
                    <FormControlLabel
                      control={<Checkbox checked={!denyUpdate} disabled={save.isPending}
                        onChange={(event) => { setDenyUpdateDraft(!event.target.checked); setSaved(null) }} />}
                      label={t('supabaseConfiguration.allowUpdate')}
                    />
                    <FormControlLabel
                      control={<Checkbox checked={!denyDelete} disabled={save.isPending}
                        onChange={(event) => { setDenyDeleteDraft(!event.target.checked); setSaved(null) }} />}
                      label={t('supabaseConfiguration.allowDelete')}
                    />
                  </Stack>
                  <TextField
                    type="number" sx={{ maxWidth: 260 }}
                    label={t('supabaseConfiguration.readLimitLabel')}
                    helperText={t('supabaseConfiguration.readLimitHelp')}
                    value={readLimitText}
                    error={readLimitDraft !== null && !readLimitValid}
                    onChange={(event) => { setReadLimitDraft(event.target.value); setSaved(null) }}
                    disabled={save.isPending}
                    inputProps={{ min: 1, max: 10000 }}
                  />

                  <Divider />
                  <Typography variant="subtitle2">{t('supabaseConfiguration.tablesTitle')}</Typography>
                  <Typography variant="body2" color="text.secondary">{t('supabaseConfiguration.tablesHelp')}</Typography>
                  {tables.length === 0 && (
                    <Alert severity="warning">{t('supabaseConfiguration.noTables')}</Alert>
                  )}
                  {tables.map((table) => (
                    <Card key={table.id} variant="outlined" sx={{ bgcolor: 'action.hover' }}>
                      <CardContent>
                        <Stack spacing={1.5}>
                          {/* Name, collapse toggle and remove stay visible regardless of
                              collapsed state - a table added by mistake must be removable,
                              and an empty name must stay visible, without expanding anything. */}
                          <Stack direction="row" spacing={1} alignItems="center">
                            <Button size="small" sx={{ minWidth: 40 }} disabled={save.isPending}
                              onClick={() => touchTables(tables.map((t2) => t2.id !== table.id ? t2 : { ...t2, collapsed: !t2.collapsed }))}>
                              {table.collapsed ? '▸' : '▾'}
                            </Button>
                            <TextField
                              fullWidth required
                              label={t('supabaseConfiguration.tableNameLabel')}
                              value={table.name}
                              error={table.name.trim() === ''}
                              disabled={save.isPending}
                              onChange={(event) => touchTables(tables.map((t2) => t2.id !== table.id ? t2 : { ...t2, name: event.target.value }))}
                            />
                            {table.collapsed && (
                              <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
                                {t('supabaseConfiguration.columnsCount', { count: table.columns.length })}
                              </Typography>
                            )}
                            <Button color="error" disabled={save.isPending}
                              onClick={() => touchTables(tables.filter((t2) => t2.id !== table.id))}>
                              {t('supabaseConfiguration.removeTable')}
                            </Button>
                          </Stack>
                          <Collapse in={!table.collapsed}>
                          <Stack spacing={1.5}>
                          <Typography variant="body2" color="text.secondary">{t('supabaseConfiguration.columnsTitle')}</Typography>
                          {table.columns.map((column) => (
                            <Stack key={column.id} spacing={1} sx={{ pl: 1, borderLeft: '2px solid', borderColor: 'divider' }}>
                              <Stack direction="row" spacing={1} alignItems="flex-start" flexWrap="wrap">
                                <TextField
                                  required
                                  label={t('supabaseConfiguration.columnNameLabel')}
                                  value={column.name}
                                  error={column.name.trim() === ''}
                                  disabled={save.isPending}
                                  onChange={(event) => updateColumn(table.id, column.id, { name: event.target.value })}
                                />
                                <TextField
                                  select
                                  label={t('supabaseConfiguration.columnTypeLabel')}
                                  helperText={t('supabaseConfiguration.columnTypeHelp')}
                                  value={column.type}
                                  disabled={save.isPending}
                                  sx={{ minWidth: 220 }}
                                  onChange={(event) => updateColumn(table.id, column.id, { type: event.target.value as ColumnType })}
                                >
                                  {COLUMN_TYPES.map((type) => (
                                    <MenuItem key={type} value={type}>{t(`supabaseConfiguration.columnType.${type}`)}</MenuItem>
                                  ))}
                                </TextField>
                                <FormControlLabel
                                  control={<Checkbox checked={column.required} disabled={save.isPending}
                                    onChange={(event) => updateColumn(table.id, column.id, { required: event.target.checked })} />}
                                  label={t('supabaseConfiguration.columnRequired')}
                                />
                                <FormControlLabel
                                  control={<Checkbox checked={column.writable} disabled={save.isPending}
                                    onChange={(event) => updateColumn(table.id, column.id, { writable: event.target.checked })} />}
                                  label={t('supabaseConfiguration.columnWritable')}
                                />
                                <FormControlLabel
                                  control={<Checkbox checked={column.filterable} disabled={save.isPending}
                                    onChange={(event) => updateColumn(table.id, column.id, { filterable: event.target.checked })} />}
                                  label={t('supabaseConfiguration.columnFilterable')}
                                />
                                <Button size="small" color="error" disabled={save.isPending}
                                  onClick={() => touchTables(tables.map((t2) => t2.id !== table.id ? t2 : {
                                    ...t2, columns: t2.columns.filter((c2) => c2.id !== column.id),
                                  }))}>
                                  {t('supabaseConfiguration.removeColumn')}
                                </Button>
                              </Stack>
                              <Button size="small" sx={{ alignSelf: 'flex-start' }} disabled={save.isPending}
                                onClick={() => updateColumn(table.id, column.id, { advancedOpen: !column.advancedOpen })}>
                                {t(column.advancedOpen ? 'supabaseConfiguration.advancedHide' : 'supabaseConfiguration.advancedShow')}
                              </Button>
                              {column.advancedOpen && (
                                <Stack direction="row" spacing={1} flexWrap="wrap">
                                  <TextField
                                    label={t('supabaseConfiguration.enumLabel')}
                                    helperText={t('supabaseConfiguration.enumHelp')}
                                    value={column.enumText}
                                    disabled={save.isPending}
                                    sx={{ minWidth: 220 }}
                                    onChange={(event) => updateColumn(table.id, column.id, { enumText: event.target.value })}
                                  />
                                  <TextField
                                    label={t('supabaseConfiguration.patternLabel')}
                                    helperText={t('supabaseConfiguration.patternHelp')}
                                    value={column.pattern}
                                    error={column.pattern.trim() !== '' && (() => { try { new RegExp(column.pattern.trim()); return false } catch { return true } })()}
                                    disabled={save.isPending}
                                    sx={{ minWidth: 220 }}
                                    onChange={(event) => updateColumn(table.id, column.id, { pattern: event.target.value })}
                                  />
                                  <TextField
                                    type="number" label={t('supabaseConfiguration.minLengthLabel')}
                                    value={column.minLength} disabled={save.isPending} sx={{ maxWidth: 140 }}
                                    onChange={(event) => updateColumn(table.id, column.id, { minLength: event.target.value })}
                                  />
                                  <TextField
                                    type="number" label={t('supabaseConfiguration.maxLengthLabel')}
                                    value={column.maxLength} disabled={save.isPending} sx={{ maxWidth: 140 }}
                                    onChange={(event) => updateColumn(table.id, column.id, { maxLength: event.target.value })}
                                  />
                                </Stack>
                              )}
                            </Stack>
                          ))}
                          <Button size="small" sx={{ alignSelf: 'flex-start' }} disabled={save.isPending}
                            onClick={() => touchTables(tables.map((t2) => t2.id !== table.id ? t2 : {
                              ...t2, columns: [...t2.columns, emptyColumn()],
                            }))}>
                            {t('supabaseConfiguration.addColumn')}
                          </Button>
                          </Stack>
                          </Collapse>
                        </Stack>
                      </CardContent>
                    </Card>
                  ))}
                  <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} disabled={save.isPending}
                    onClick={() => touchTables([...tables, emptyTable()])}>
                    {t('supabaseConfiguration.addTable')}
                  </Button>

                  <Button type="submit" variant="contained"
                    disabled={!projectUrl.trim() || !allowlistValid || (!hasKeyForMode && !keyDraft.trim()) || save.isPending}>
                    {t(save.isPending ? 'supabaseConfiguration.saving' : 'supabaseConfiguration.save')}
                  </Button>
                </Stack>
              ) : (
                <Alert severity="info">{t('supabaseConfiguration.readOnlyHint')}</Alert>
              )}
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}
