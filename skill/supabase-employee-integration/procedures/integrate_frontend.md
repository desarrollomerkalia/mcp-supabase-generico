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

## 4c. Montar la verificación de canal (WhatsApp) - mismo condicional que el login (sin divergencia)

Igual que el login personal, esto solo aplica cuando el modo de acceso
configurado es `rls_personal` - sin sesión por usuario no hay nada que
vincular a un canal. Permite que una conversación de WhatsApp (u otro
canal) use las herramientas con la identidad verificada de quien escribe,
en vez de quedar limitado al chat web - ver el punto 6 de la sección de
verificación al inicio de este skill para el porqué completo.

1. Copia `assets/frontend/components/SupabaseChannelVerifyForm.tsx` y
   `assets/frontend/services/supabaseReturn.ts` tal cual, sin modificar su
   contenido, a `src/frontend/src/components/` y
   `src/frontend/src/services/` respectivamente.
2. En el paso 2 (fusión de traducciones), el bloque `supabaseChannelVerify`
   y los códigos de error `supabase_channel_not_verified`/
   `supabase_channel_token_invalid`/`supabase_public_base_url_invalid`/
   `supabase_public_base_url_required` ya vienen incluidos en los
   fragmentos `assets/frontend/i18n/es.ts`/`en.ts` de este skill - no hace
   falta un paso de fusión aparte, solo no perderlos al copiar solo un
   subconjunto de claves.
3. **Monta la ruta `/supabase-canal-verificar` FUERA del `<Layout>`**, en la
   misma rama sin sesión/pública de `App.tsx` donde vive `/login` - nunca
   dentro del árbol que usa `<Layout>`. Motivo verificado contra un
   empleado real: `Layout.tsx` (invariante) redirige en móvil cualquier
   ruta que no sea `/agent`, lo que rompe tanto esta pantalla como
   `/supabase-login` si quedan dentro de ese árbol. Usa
   `SupabaseChannelVerifyForm` protegido por el mismo mecanismo de sesión
   WAI que ya usa `/supabase-login` (sigue exigiendo sesión WAI - la
   identidad de quien vincula el canal nunca se relaja a "token únicamente",
   ver `procedures/channel_verification_ux.md` para el porqué de seguridad).
4. Conecta `supabaseReturn.ts` al flujo de login de WAI para que el
   `?token=...` de la URL original sobreviva el ir-y-volver de
   `/login`: antes de redirigir a login sin sesión, guarda el destino con
   `rememberSupabaseReturn(supabaseDestination(pathname, search), true)`;
   al volver ya autenticado a la raíz, si `getSupabaseReturn()` devuelve un
   destino pendiente, navega ahí y limpia con `clearSupabaseReturn()`. No
   dupliques esta lógica a mano - son las únicas tres funciones exportadas
   del archivo, ya probadas.
5. Agrega la ruta en `App.tsx`:
   ```tsx
   <Route path="/supabase-canal-verificar" element={<SupabaseChannelVerifyForm />} />
   ```
   en la misma rama donde montaste `/supabase-login`.
6. En el paso 4b (`self_knowledge.py`), agrega también una entrada para
   `/supabase-canal-verificar` junto a la de `/supabase-login` - mismo
   protocolo de divergencia, no una entrada por separado.
7. Un administrador debe configurar la URL pública del empleado (campo
   nuevo en `SupabaseConfigurationForm`, Operaciones → Conectores →
   Supabase) para que el enlace que se envía por el canal sea una URL
   absoluta y tocable - sin eso, el backend devuelve
   `supabase_public_base_url_required` en vez de un enlace roto. Esto no
   es parte de este procedimiento de frontend (es configuración de
   negocio, como la URL del proyecto Supabase), pero repórtalo como
   pendiente de configurar si el empleado va a usar canales.

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
