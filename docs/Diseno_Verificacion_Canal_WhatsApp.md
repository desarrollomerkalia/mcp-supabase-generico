# Diseño: sesión personal de Supabase verificada desde canales (WhatsApp)

## Objetivo

Permitir que una conversación de WhatsApp use `rls_personal` de verdad -
no reusando la libreta de contactos (`AppUser.phones_json`, sin ninguna
prueba de identidad), sino un vínculo **verificado**: el número de
WhatsApp queda asociado a un email de Supabase solo después de que esa
persona demuestre su contraseña real, una vez, a través de un link.

## Por qué son dos piezas, no una

Ya confirmamos que `tools.py` exige `AgentRun.mode == "chat"` antes de
dejar pasar cualquier tool - WhatsApp corre con `mode == "autonomous"`.
Un link de login por sí solo no basta: hay que (1) crear el vínculo
verificado, y (2) enseñarle a `tools.py` a aceptar una llamada de canal
cuando ese vínculo específico existe - nunca de forma general.

## Principio de diseño: nada de tabla nueva en el host

Todo lo que `sgmcp_adapter` ya guarda (la configuración de conexión
completa) vive como un blob cifrado dentro de `AppSetting`, una tabla que
la plantilla ya trae - el adaptador nunca le pide al host correr una
migración nueva. Los vínculos de canal siguen la misma regla: se guardan
como otro blob cifrado en `AppSetting`, bajo una clave nueva
(`sgmcp_adapter_channel_bindings`), no una tabla propia. Esto mantiene
intacto lo que hace a este skill instalable con `pip install -e` sin
tocar el esquema de base de datos de nadie.

## Componentes

### 1. `sgmcp_adapter/channel_binding.py` (nuevo)

- `sign_verification_token(channel_id, external_identity, *, ttl_s=1800) -> str`:
  token firmado (HMAC con una clave ya disponible en el host - revisar si
  `JWT_SECRET_KEY` es reusable o si hace falta una propia; sin agregar
  dependencias nuevas, con `hmac`/`hashlib` de la librería estándar) que
  codifica canal, identidad externa (teléfono canónico) y expiración.
- `verify_token(token) -> (channel_id, external_identity) | None`: valida
  firma y expiración: de un solo uso implícito porque el binding, una vez
  escrito, ya no necesita que se vuelva a usar ese token (se descarta).
- `get_bindings(session) -> dict`, `set_binding(session, channel_id, external_identity, principal)`,
  `revoke_binding(session, channel_id, external_identity)`: leen/escriben
  el blob cifrado en `AppSetting` bajo la clave dedicada, con el mismo
  mecanismo de cifrado que ya usa `integration.py` para la configuración
  principal.
- `resolve_channel_principal(session, channel_id, external_identity) -> str | None`:
  busca un binding vigente (no revocado) y devuelve el principal
  verificado, o `None`.

### 2. `sgmcp_adapter/tools.py` - ajuste del gate existente

Hoy, si el run no es `mode == "chat"`, se rechaza con 401 sin más. Nuevo
comportamiento:

1. Si `mode == "chat"`: igual que siempre, sin cambios.
2. Si `mode != "chat"` (canal): resolver `canal_id`/`solicitante` (ya
   existen en `AgentRun` - `canal_id`, `solicitante`, confirmados en la
   investigación) y canonizar el teléfono. Buscar un binding vigente con
   `resolve_channel_principal`.
   - Si hay binding: usar ese principal para todo lo demás - el resto del
     flujo (sesión, RLS, allowlist) es exactamente el mismo código que ya
     usa el chat web, sin duplicar lógica.
   - Si NO hay binding: en vez del 401 genérico, generar un link con
     `sign_verification_token` y devolver un mensaje claro para que el
     modelo se lo pase al usuario ("para consultar el CRM desde WhatsApp,
     verifica tu cuenta aquí: <link>, vence en 30 minutos").

### 3. `sgmcp_adapter/routes.py` - nuevo endpoint

`POST /api/supabase/channel-session`: recibe `{token, email, password}`.
Hace EXACTAMENTE lo mismo que el login normal de `/api/supabase/session`
(autentica contra Supabase de verdad, nunca confía en lo que mande el
cliente) y, solo si el login es exitoso, además valida el `token` con
`verify_token` y escribe el binding con `set_binding`. Si el token es
inválido/expiró, el login en sí puede completarse igual (la persona sí
puede iniciar sesión normal desde el chat web), pero el binding no se
crea - se le informa explícitamente.

### 4. `assets/frontend/components/SupabaseChannelVerifyForm.tsx` (nuevo)

Página montada en una ruta nueva, ej. `/supabase-canal-verificar?token=...`
- lee el token de la URL, muestra el mismo formulario de email/contraseña
que `SupabaseLoginForm.tsx`, pero llama al endpoint nuevo en vez del de
login normal. Mensaje de éxito: "Tu número de WhatsApp ya puede consultar
el CRM con tu sesión personal."

### 5. Administración: ver y revocar vínculos

Extender `SupabaseConfigurationForm.tsx` (o una sección nueva) con una
lista de bindings activos (canal, teléfono parcialmente enmascarado,
email, fecha) y un botón de revocar - para cuando un número cambie de
dueño o haya que cortar el acceso.

## Lo que NO cambia

- `service_role` no se toca - este mecanismo es exclusivo de
  `rls_personal`.
- El chat web sigue funcionando exactamente igual - el gate de
  `mode == "chat"` no se modifica, solo se le agrega una rama nueva para
  `mode != "chat"` con binding.
- Ninguna tabla nueva en el host, ningún cambio de esquema.

## Riesgos a vigilar al construir

- El token debe ser de corta duración y efectivamente de un solo uso
  (una vez escrito el binding, un token reutilizado no debe poder
  reescribirlo con otro principal sin pasar login de nuevo).
- Limitar cuántos links se pueden generar por número/tiempo, para evitar
  abuso.
- El binding nunca se crea sin que el login real de Supabase haya tenido
  éxito primero - el token por sí solo nunca basta.

## Plan de construcción, en dos fases

**Fase 1 (aislada, de bajo riesgo):** `channel_binding.py` completo +
pruebas unitarias (firma/verificación de token, guardar/leer/revocar
binding) + el endpoint nuevo + el formulario de verificación. Nada de
esto toca el gate de `tools.py` todavía - se puede construir y probar
igual que cualquier otra pieza nueva del adaptador, sin tocar el camino
que ya funciona.

**Fase 2 (el cambio sensible):** modificar el gate de `tools.py` para
usar bindings en runs de canal. Esta fase sí se revisa con cuidado antes
de tocar código, por ser el punto que decide quién puede leer/escribir
datos personales.

## Archivos a tocar (resumen)

- Nuevo: `adapter/sgmcp_adapter/channel_binding.py`
- Modificar: `adapter/sgmcp_adapter/tools.py`, `adapter/sgmcp_adapter/routes.py`
- Nuevo: `adapter/frontend/components/SupabaseChannelVerifyForm.tsx`
- Modificar: `adapter/frontend/components/SupabaseConfigurationForm.tsx` (lista/revocar)
- Pruebas nuevas en `adapter/tests/test_adapter_smoke.py`
- Documentar en `procedures/integrate_frontend.md` (ruta nueva, divergencia si aplica)
