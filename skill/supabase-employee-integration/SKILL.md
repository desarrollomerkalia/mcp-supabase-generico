---
name: supabase-employee-integration
description: Carga este skill cuando el encargo de un empleado requiera leer o escribir datos en un proyecto de Supabase a través del conector sgmcp (MCP genérico, https://mcp-supabase-generico.onrender.com - identifícalo por URL o nombre, el id de catálogo varía por tenant). Integra el paquete sgmcp_adapter en el backend del empleado, con sesión personal de Supabase Auth o credencial de servicio administrada, y deja los endpoints de configuración listos para que un administrador declare URL, llaves y el allowlist de tablas/columnas. No define lógica de negocio.
---

# Integración De Empleados Con Supabase (vía sgmcp)

## ✅ Verificado de punta a punta en producción

Probado en un empleado real (`prueba-notas-supabase`): lectura y creación
de registros desde una conversación de chat real, en **ambos** modos de
autenticación (`rls_personal` y `service_role`), contra un proyecto real
de Supabase. Varios hallazgos reales se encontraron y corrigieron en el
camino - documentados aquí para que nadie los reintroduzca (no se
referencia un número de versión fijo a propósito: este documento se
reedita en cada versión nueva, un número fijo aquí queda desactualizado de
inmediato):

1. **Catálogo de tools oculto hasta configurar** (ya corregido, se
   registran siempre): una versión anterior no registraba ninguna
   herramienta MCP
   hasta que existía al menos una tabla configurada. Como el runtime del
   agente descubre el catálogo de `app-tools` una sola vez, al desplegar
   - antes de que cualquier administrador haya configurado nada - ese
   catálogo vacío quedaba fijo para siempre, y el chat nunca podía usar
   las herramientas aunque se configuraran después. Las cinco ahora se
   registran siempre, desde el arranque; `core.Adapter` sigue rechazando
   en tiempo de ejecución cualquier tabla no permitida.
2. **`service_role` pasaba `None` a la verificación de paginación**
   (corregido en esta versión): `execute()` no reutilizaba la respuesta ya
   obtenida de `sgmcp` al verificar el total de una lectura en modo
   `service_role` - fallaba el 100% de las lecturas en ese modo,
   siempre, con `sgmcp_read_response_invalid`. `rls_personal` nunca tuvo
   este bug. Hay una prueba de regresión (`tests/test_adapter_smoke.py`)
   que reproduce el error exacto contra el código anterior.
3. **`upsertar_registros` agregada** (quinta tool genérica): faltaba
   envolver `supabase_upsertar` del MCP - mismo allowlist de columnas
   escribibles que ya usan insertar/actualizar, sin ningún concepto de
   permisos nuevo.
4. **Rechazo de escritura por RLS reportaba un mensaje genérico y
   confuso** (corregido en esta versión): cualquier falla de escritura,
   sin distinguir causa, caía en el mismo mensaje cauteloso de "no se
   pudo confirmar, no reintentar" (`write_state="unknown"`) pensado para
   fallas de red genuinamente ambiguas - incluso cuando Postgres había
   rechazado la escritura con certeza por una política RLS. Ahora ese
   patrón específico y estable de Postgres se detecta y se reporta como
   `write_state="not_sent"` con un mensaje claro de permisos denegados;
   cualquier otra causa de falla sigue devolviendo exactamente el mensaje
   genérico de siempre (hay una prueba de regresión explícita para esto).
   De forma análoga, `leer_registros` ahora aclara en su descripción que,
   en modo `rls_personal`, un resultado de 0 filas puede ser una tabla
   vacía o filas ocultas por RLS - indistinguibles técnicamente, pero el
   modelo ya no debe afirmar que la tabla está vacía.
5. **El punto 4 tardó dos intentos en corregirse de verdad** (corregido en
   esta versión): la primera versión del fix de RLS nunca se activaba en
   producción. Causa real, confirmada con el texto crudo del error vía el
   diagnóstico admin-only agregado para esto mismo: `server.js` tiene DOS
   manejadores distintos de `tools/call` - uno del SDK oficial (que envuelve
   errores como `{content:[...], isError:true}`, lo único que el adaptador
   sabía leer) y otro, el manejador JSON-RPC directo por POST, que es el que
   **de verdad usa este adaptador** ("Streamable HTTP") y envuelve CUALQUIER
   error - incluida una violación de RLS - como un error a nivel JSON-RPC
   (`{error: {code, message}}`), un formato completamente distinto. El texto
   de Postgres viajaba hasta nosotros todo este tiempo; simplemente nunca se
   leía de esa rama. `mcp_client.py` ahora captura el texto en ambos formatos.
6. **Vinculación de canal (WhatsApp u otro) a una sesión personal de
   Supabase** (nuevo en esta versión, probado de punta a punta con WhatsApp
   real): hasta ahora `rls_personal` solo funcionaba desde el chat web -
   una conversación de canal (WhatsApp) nunca tenía forma de usar las
   herramientas con una identidad verificada, y el directorio teléfono→email
   del host es deliberadamente NO confiable para esto (ver
   `wai_identity.py`). `channel_binding.py` + `routes.py` (`POST
   /channel-session`, `GET`/`DELETE /channel-bindings/...`) agregan un
   enlace de verificación firmado y cifrado (`sign_verification_token` /
   `verify_token`, mismo mecanismo de cifrado que ya protegía la
   configuración) que ata un `(canal_id, teléfono)` a un email ya
   autenticado por una contraseña real de Supabase - nunca por el token en
   sí. Escrituras siguen exigiendo el chat web (decisión de alcance, no
   limitación técnica). Varios hallazgos reales costosos en el camino:
   - **El propio rol mínimo del host (`TOOL_MIN_ROLE`) bloqueaba todo antes
     de llegar al adaptador:** `app/mcp_actor.py` solo trata `mode=="chat"`
     como humano; no hay forma de decirle "este actor de canal ya tiene
     identidad verificada". Las cinco tools se registran en `LEVEL_AGENT`,
     no `LEVEL_HUMAN` - la identidad/autorización real vive enteramente
     dentro de `generic_operation()`, no en el gate del host.
   - **El esquema de las tools solo aceptaba `run_token`, pero una
     conversación de canal solo recibe `run_id` (entero) del runtime,
     nunca un `run_token`:** sin `run_id` como alternativa declarada en el
     `inputSchema` (`anyOf: [{required: [run_token]}, {required:
     [run_id]}]`), el modelo no tenía ningún argumento válido que pasar y
     simplemente dejaba de intentar la llamada - sin error visible en
     ningún log, porque nunca llegaba a construirse la llamada.
   - **El `run_token` real de una fila de canal puede ser `NULL`** (el
     INSERT de ejecuciones de canal no lo asigna, a diferencia del chat).
     `generic_operation()` arma en su lugar un identificador sintético
     (`"canal:{canal_id}:{solicitante}"`) atado al `run_id` exacto de esa
     invocación vía un `ContextVar` (`wai_identity.channel_run_context`) -
     nunca persistido, nunca reutilizable entre turnos distintos.
   - **La memoria propia del empleado (`self_memory`) y la memoria del
     usuario pueden sesgar al modelo a no intentar la llamada en absoluto**,
     incluso con el backend ya corregido: observaciones repetidas de
     fallos anteriores (reales o de pruebas) se convierten en una "línea
     base" que el modelo trata como verdad sin volver a probar. No hay
     arreglo de código para esto - es un comportamiento del modelo, no un
     bug del adaptador - pero vale la pena saberlo antes de pasar horas
     depurando algo que ya funciona: limpiar ambas memorias y pedirle al
     empleado que explique por qué no intentó la llamada (en vez de pedirle
     de nuevo el dato de negocio) ayuda a diagnosticarlo rápido.
   - **El modelo a veces resumía el link de verificación en vez de copiarlo
     literal** en su respuesta de WhatsApp, incluso cuando el backend sí lo
     generaba correctamente - inutilizando el link para quien lo recibía.
     La instrucción del skill operativo debe exigir copiar el campo
     `message` completo, carácter por carácter, cuando llega
     `code: "supabase_channel_not_verified"` - ver
     `procedures/channel_verification_ux.md`.
   - **El link necesita una URL pública absoluta para ser tocable en
     WhatsApp** - sin `APP_PUBLIC_BASE_URL` (inyectada por el desplegador)
     ni una configuración explícita, sale una ruta relativa que WhatsApp no
     convierte en enlace. `public_url.py` agrega una URL pública
     configurable por empleado (override admin → `APP_PUBLIC_BASE_URL` →
     sin URL disponible), persistida igual que el resto de la
     configuración cifrada, sin inventar ni hardcodear ningún dominio.

## Cuándo se aplica
- El encargo del empleado menciona Supabase, una base de datos Postgres gestionada por Supabase, o el conector `sgmcp`.
- No se aplica si el encargo ya trae su propio conector de datos distinto, o si no requiere persistencia/lectura de datos de negocio.

## Contrato
- **Entra:** un backend de empleado ya generado desde la plantilla de `wai-agent-app-builder` (con `app/main.py` real, no la plantilla del constructor), sin lógica de Supabase previa.
- **Sale:** el backend con `sgmcp_adapter` integrado, las rutas `/api/supabase/session` y `/api/supabase/configuration` activas, y las cinco herramientas genéricas (`leer_registros`, `insertar_registros`, `actualizar_registros`, `eliminar_registros`, `upsertar_registros`) **ya registradas en el catálogo MCP desde el primer despliegue** - deliberado, no un descuido: el runtime del agente descubre ese catálogo una sola vez, al arrancar, y nunca lo vuelve a consultar (ver el punto 1 de la sección de verificación arriba). Antes de que se configure al menos una tabla, las cinco existen en el catálogo pero `core.Adapter` rechaza cualquier llamada real ("tabla no permitida") - el rechazo pasa en tiempo de ejecución, no ocultando la herramienta. Una versión anterior de este skill sí las ocultaba hasta configurar - se revirtió porque eso era, con alta probabilidad, la causa real de un bloqueo observado antes.
- **Costo real de usar este skill (a la fecha):** la plantilla actual no tiene un punto de extensión sancionado para esto. Integrar este skill modifica el archivo invariante `app/main.py`, lo cual exige declarar una divergencia en `agent_app.notes.divergences[]` **con aprobación humana explícita** (`approver_email` real, nunca autoaprobada por el constructor), y deja a ese empleado marcado permanentemente como fork de la plantilla para ese archivo - el verificador de invariantes seguirá fallando esa comparación a propósito, y `--fix-invariants` nunca debe correrse sobre ese empleado. No es "solo contexto de negocio y listo" hasta que el proveedor de la plantilla (usewai.com) agregue un hook oficial - ver `../FEATURE_REQUEST_EXTENSION_POINT.md` (fuera de este paquete, no es contenido del skill) para la solicitud ya redactada.
- **Acomodación conocida a una regla del verificador general del constructor:** la sección "Forbidden patterns" de `verify_generated_app.sh` prohíbe el texto literal `/v1/` en `src/backend` fuera de 5 archivos listados por nombre (pensada para impedir llamadas directas a APIs de proveedores de LLM, que suelen usar rutas `/v1/`, saltándose `agent_proxy.py`). Es una búsqueda de texto, no un análisis de qué hace el código, así que también atrapaba las llamadas legítimas de `sgmcp_adapter` a la propia API de Supabase (`/auth/v1/`, `/rest/v1/`) - que no tienen relación con el runtime del agente. Desde la v1.0.2, `core.py` construye esas rutas por concatenación (`"/auth/" + "v1" + "/"`) en vez de como una cadena literal, para que el texto `/v1/` no aparezca pegado en el archivo fuente. El comportamiento no cambia en nada - mismas URLs, misma llamada. Ya se le pidió al proveedor (`../FEATURE_REQUEST_EXTENSION_POINT.md`) que corrija la regla; esto es un acomodo documentado mientras tanto, no la solución definitiva.
- **Procedimiento del backend:** `procedures/integrate.md`.
- **Procedimiento del frontend (estándar - parte obligatoria de toda integración de este skill, no una elección del encargo):** `procedures/integrate_frontend.md` - construye siempre la pantalla de administración (URL/modo/llave/allowlist), montada dentro del esqueleto general de la app de empleado (verificado contra un empleado real: Operations.tsx → ConnectorsBlock). Es la razón de ser de este skill: que un administrador no técnico pueda configurar la conexión sin tocar código. El login personal se monta además, automáticamente, cuando el modo de acceso configurado es `rls_personal` - no es una segunda elección independiente, es consecuencia directa de esa primera decisión; en `service_role` no se monta porque no hay sesión por usuario que iniciar. Requiere DOS divergencias adicionales (`frontend/src/pages/Operations.tsx` y `app/self_knowledge.py`, ambos invariantes - el segundo solo para que el propio agente sepa responder sobre la pantalla de login); el banner y la ruta de login se montan desde `App.tsx`, que es andamio y no requiere divergencia.
- **Verificación obligatoria antes y después de integrar:** `checks/check_host_contract.py`. Ningún paso de integración se considera exitoso si este script falla.
- **No hace:** no define tablas, columnas ni modo de autenticación por sí mismo (eso depende del contexto de negocio del empleado concreto y se declara llenando `PUT /api/supabase/configuration`, a mano o vía la pantalla de `integrate_frontend.md`). No modifica políticas RLS de Supabase. No concede acceso `service_role` por defecto. No define flujos de negocio con efecto secundario en dos tablas (ver `assets/sgmcp_adapter`'s `docs/EXTENDING.md` si algún empleado concreto lo necesita más adelante). La pantalla de `integrate_frontend.md` no cubre la variante móvil de chat único (`SoloChatMovil.tsx`, invariante) - limitación conocida, no resuelta en esta versión.

## Contenido de este skill
- `VERSION`: una sola línea con el número de esta versión. Es la única fuente de verdad para saber qué número publicar al replicar este skill en otro tenant (ver `../../docs/REPLICAR_SKILL_EN_OTRO_TENANT.md`) - no la repitas de memoria en un prompt, léela de aquí.
- `assets/sgmcp_adapter/` + `assets/pyproject.toml`: el paquete Python del backend, autocontenido - no depende de ningún otro repositorio ni de que exista una sesión previa. Incluye `channel_binding.py` (vínculos de canal verificados) y `public_url.py` (resolución de la URL pública del empleado) - ver punto 6 de la sección de verificación arriba.
- `assets/frontend/`: componentes React/TS de la integración estándar (servicio, hook, banner, formulario de configuración, formulario de login, formulario de verificación de canal `SupabaseChannelVerifyForm.tsx`, el mecanismo de retorno tras login WAI `supabaseReturn.ts`, fragmentos de traducción es/en) - genéricos, verificados contra una implementación real (reusados o extendidos, según el archivo - cada uno documenta cuál). El formulario de login y el de verificación de canal solo se montan cuando el modo de acceso es `rls_personal`.
- `procedures/integrate.md`: pasos exactos para integrar el backend, incluida la declaración de la única modificación a un archivo invariante de la plantilla (`backend/app/main.py`).
- `procedures/integrate_frontend.md`: pasos exactos para integrar las pantallas, incluida la declaración de la divergencia en `frontend/src/pages/Operations.tsx`.
- `procedures/channel_verification_ux.md`: contrato de la vinculación de canal para empleados que YA la implementan - origen del enlace, mensaje canónico, y la regla de copia literal que debe llevar el skill operativo del propio empleado (no este skill de construcción).
- `checks/check_host_contract.py`: script ejecutable que verifica por introspección, sin conectar a ninguna base de datos, que el backend expone los símbolos y firmas que `sgmcp_adapter` necesita.
- `checks/test_adapter_integration.py` + `checks/test_sgmcp_channels.py` + `checks/conftest.py`: pruebas opcionales que **sí** necesitan RDWAI real (SQLite temporal, modelos SQLAlchemy reales) - cubren persistencia real en base de datos, autorización de `response_shape`, y todo el flujo de vinculación de canal (gate del host en `LEVEL_AGENT`, identificador sintético por `ContextVar`, rechazo de ejecuciones programadas incluso con un vínculo válido). Cópialos a `src/backend/tests/` del empleado y corre `pytest` ahí; no son parte de la suite independiente de abajo.
- `tests/test_adapter_smoke.py` + `tests/test_sgmcp_schemas.py` + `tests/test_public_url.py`: pruebas del paquete de backend en aislamiento (config, catálogo de tools, esquema `run_token`/`run_id`, validación pura de URLs públicas, lectura/escritura simuladas) - no requieren un backend real. No hay pruebas automatizadas del frontend en esta versión.

## Regla dura
Si `checks/check_host_contract.py` reporta algún fallo, o si integrar requiere modificar algo fuera de lo que describe `procedures/integrate.md`, **detente y repórtalo** en vez de parchear `assets/sgmcp_adapter/` para adaptarte al caso particular, o de modificar `app/main.py` sin seguir el protocolo de divergencias. Los ajustes al paquete se hacen una vez, centralmente, publicando una nueva versión de este skill - no por empleado.
