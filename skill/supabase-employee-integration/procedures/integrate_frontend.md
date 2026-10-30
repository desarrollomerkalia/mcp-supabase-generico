# Procedimiento De Integración Del Frontend

Sigue estos pasos en orden, después de completar `integrate.md` (el backend
debe estar ya integrado y verificado). Verificado contra
`wai-agent-app-builder` 12.59.1 - si la plantilla cambió de versión desde
entonces, confirma antes de aplicar nada a ciegas.

## 0. Precondición

El backend de este empleado ya pasó `integrate.md` completo (checks del
host en verde, divergencia de `backend/app/main.py` ya declarada y
aprobada). Si no, resuélvelo primero.

## 1. Copiar los archivos nuevos (sin divergencia - son archivos nuevos, no ediciones a invariantes)

Copia tal cual, sin modificar su contenido:

| Origen (este skill) | Destino en el empleado |
|---|---|
| `assets/frontend/services/supabaseApi.ts` | `src/frontend/src/services/supabaseApi.ts` |
| `assets/frontend/hooks/useSupabaseSession.ts` | `src/frontend/src/hooks/useSupabaseSession.ts` |
| `assets/frontend/components/SupabaseConfigurationForm.tsx` | `src/frontend/src/components/SupabaseConfigurationForm.tsx` |
| `assets/frontend/components/SupabaseLoginForm.tsx` | `src/frontend/src/components/SupabaseLoginForm.tsx` |
| `assets/frontend/components/SupabaseSessionBanner.tsx` | `src/frontend/src/components/SupabaseSessionBanner.tsx` |

## 2. Fusionar las traducciones (verifica primero que no sean invariantes)

Antes de editar, confirma en la lista ejecutable del verificador que
`src/frontend/src/i18n/es.ts` y `.../i18n/en.ts` NO están clasificados
como invariantes (se asume que no lo están, porque empleados existentes ya
los personalizan con copy de negocio - pero confírmalo contra la lista
real antes de tocarlos; si alguno sí es invariante, DETENTE y repórtalo en
vez de editarlo).

Si no son invariantes: fusiona las tres claves de nivel superior
(`supabaseConfiguration`, `supabaseSession`, `supabaseLogin`) de
`assets/frontend/i18n/es.ts` dentro del objeto de traducciones real en
`src/frontend/src/i18n/es.ts`, y lo mismo para `en.ts`. No reemplaces el
archivo completo - solo añade/fusiona esas tres claves.

## 3. Montar el formulario de configuración en Operations.tsx (ESTO sí requiere divergencia)

`src/frontend/src/pages/Operations.tsx` es invariante. El formulario de
configuración debe quedar dentro de `ConnectorsBlock`, como hermano
colocado **antes** de `<McpConnectorsSection />` (mismo patrón ya probado
en un empleado real: Supabase tiene su propio backend, no se integra al
mecanismo genérico de conectores Composio).

Sigue el mismo protocolo de divergencia que ya usaste para
`backend/app/main.py`:

1. Obtén aceptación humana explícita del fork, con un correo real de quien
   aprueba.
2. Agrega la entrada a `wai-app.json` (`agent_app.notes.divergences[]`),
   ANTES de modificar el archivo:
   ```json
   {
     "file_path": "frontend/src/pages/Operations.tsx",
     "reason": "Montar el formulario de configuracion de Supabase (sgmcp_adapter).",
     "rationale": "Se agrega SupabaseConfigurationForm dentro de ConnectorsBlock, como hermano antes de McpConnectorsSection. La plantilla actual no ofrece un punto de extension para esto. Se acepta mantener esta diferencia respecto de la baseline.",
     "date": "AAAA-MM-DD",
     "approver_email": "correo-real-de-quien-aprueba@dominio"
   }
   ```
3. Modifica `Operations.tsx`: agrega el import de `SupabaseConfigurationForm`
   desde `../components/SupabaseConfigurationForm`, y móntalo como primer
   hijo del `<Stack spacing={3}>` que devuelve `ConnectorsBlock`, antes de
   `<McpConnectorsSection />`. No cambies nada más de ese archivo.
4. Al correr el verificador completo, reporta por separado el fallo
   esperado de esta comparación de invariante - no es un error a corregir.
   **No ejecutes `--fix-invariants`** sobre este archivo.

## 4. Montar el banner y la ruta de login desde App.tsx (sin divergencia - es andamio)

`src/frontend/src/App.tsx` está clasificado como `SCAFFOLD_FILES`, no
invariante - se edita libremente, sin declarar nada. **No toques
`Layout.tsx`** (ese sí es invariante): monta el banner como hijo de
`<Layout>` desde `App.tsx`, sin modificar el propio `Layout.tsx`.

1. Importa `SupabaseSessionBanner` desde `../components/SupabaseSessionBanner`
   e `SupabaseLoginForm` desde `../components/SupabaseLoginForm`.
2. Dentro de `AppShell`, donde ya se envuelven las rutas autenticadas con
   `<Layout>...</Layout>`, agrega `<SupabaseSessionBanner loginPath="/supabase-login" />`
   como primer hijo, antes de `<Suspense>`/`<Routes>` (mismo lugar donde un
   empleado ya existente lo hace).
3. Agrega una ruta nueva dentro de `<Routes>`:
   ```tsx
   <Route path="/supabase-login" element={<ProtectedRoute><SupabaseLoginForm /></ProtectedRoute>} />
   ```
   Usa el mismo patrón de `ProtectedRoute` que ya usan las demás rutas
   autenticadas de este archivo.
4. **Fuera de alcance a propósito:** la variante móvil de chat único
   (`SoloChatMovil.tsx`, invariante) no muestra el banner en esta versión.
   No declares una divergencia para eso - repórtalo como limitación
   conocida, no lo resuelvas por tu cuenta.

## 4b. Declarar la ruta nueva en `app/self_knowledge.py` (SEGUNDA divergencia de este procedimiento)

`app/self_knowledge.py` también es invariante, y su `SCREENS` alimenta
tanto el índice de pantallas que recibe el agente al iniciar una
conversación (`index_block()`) como la tool MCP `describe_app` y el
endpoint `/api/manifest/app-map`. Sin esta entrada, el agente no sabe
responder "¿dónde conecto mi cuenta de Supabase?" - no rompe navegación,
autenticación ni la API de Supabase, pero sí produce una respuesta
incompleta a una pregunta razonable sobre el propio empleado.

1. Lee las entradas existentes de `SCREENS` para replicar su forma exacta
   (campos y convenciones) - no inventes una estructura nueva.
2. Sigue el mismo protocolo de divergencia (aceptación humana explícita,
   entrada completa de 5 campos en `agent_app.notes.divergences[]`,
   `file_path: "frontend/src/app/self_knowledge.py"` o la ruta relativa
   correcta según dónde viva ese archivo) antes de modificarlo.
3. Agrega una entrada para `/supabase-login` siguiendo la forma de las
   entradas existentes. No cambies nada más del archivo.

## 5. Verificar

- El frontend compila sin errores.
- El verificador completo muestra exactamente tres fallos esperados de
  comparación de invariante: `backend/app/main.py` (de `integrate.md`),
  `frontend/src/pages/Operations.tsx` y `app/self_knowledge.py` (de este
  procedimiento) - ningún otro archivo debe aparecer como
  modificado-no-declarado.
- `test_self_knowledge` debe pasar ahora que `/supabase-login` está
  declarada.
- `i18n/es.ts` y `en.ts` no deben aparecer en ninguna lista de invariantes
  violados (si el paso 2 los hubiera editado siendo invariantes, aquí
  aparecerían como fallo adicional - en ese caso, deshaz el cambio del
  paso 2 y repórtalo en vez de declarar una divergencia extra a ciegas).

## 6. Reportar

Resultado de la compilación, los tres fallos esperados (y confirmación de
que no hay un cuarto), y las tres referencias de divergencia registradas
(`backend/app/main.py`, `frontend/src/pages/Operations.tsx` y
`app/self_knowledge.py`).
