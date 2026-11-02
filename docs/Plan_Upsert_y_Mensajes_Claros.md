# Plan: agregar `upsertar_registros` y aclarar los mensajes de error de escritura/lectura

Este documento existe completo aquí (no solo en el chat) porque hubo
problemas leyendo respuestas largas. Si algo se corta en el chat, esta es
la versión íntegra.

## Lo que reportaste, en los dos casos

1. **Escritura rechazada por RLS**: intentaste insertar una nota sin
   permiso y el chat dijo *"No puedo confirmar si se creó «Prueba RLS
   3»: el registro no confirmó la escritura y la consulta para
   verificarlo fue rechazada. No la he vuelto a guardar para evitar
   duplicarla."* - técnicamente correcto pero nada claro: no dice que es
   un tema de permisos.
2. **Lectura con RLS ocultando filas**: pediste leer notas y el chat dijo
   *"Actualmente no hay notas visibles... La consulta devolvió 0
   registros"* - suena a tabla vacía, cuando en realidad sí hay filas,
   solo que RLS las oculta a ese usuario.

Investigué el código real (no es una opinión) para entender exactamente
por qué pasa esto, y hasta dónde se puede corregir sin tocar la lógica de
negocio.

## Caso 1: por qué el mensaje de escritura es tan vago (y cómo arreglarlo bien)

### Causa raíz exacta

- `server.js:290` - cuando Supabase rechaza una escritura (por RLS u
  otra razón), el servidor MCP devuelve el error como texto plano:
  `` `Error: ${error.message}` `` con `isError: true`. Para un rechazo de
  RLS, Postgres siempre devuelve el mismo texto estable:
  `"new row violates row-level security policy for table \"...\""`.
- `adapter/sgmcp_adapter/mcp_client.py:64-65` - hoy, cuando ve
  `isError: true`, **descarta ese texto** y solo guarda
  `MCPError(kind="tool")` - sin el mensaje real.
- `adapter/sgmcp_adapter/core.py:856-878` (`_mcp_failure_error`) - para
  CUALQUIER falla de escritura (`mcp_tool != "supabase_leer"`), sin
  distinguir causas, siempre devuelve el mismo mensaje genérico
  ("sgmcp no confirmo la escritura... consulta el registro") con
  `write_state = "unknown"` - el estado más cauteloso que existe, pensado
  para cuando de verdad no se puede saber si el dato se guardó o no (ej.
  la conexión se cortó a medias).

El problema de fondo: **un rechazo de RLS NO es ambiguo** - Postgres dijo
que no, con toda seguridad, antes de escribir nada. Tratarlo igual que un
fallo de red real (donde sí hay incertidumbre genuina) es lo que produce
el mensaje confuso - el adaptador está siendo más cauteloso de lo que la
situación requiere, no al revés.

### El arreglo (acotado, no toca la lógica de escritura/idempotencia)

1. `mcp_client.py`: cuando `isError: true`, capturar el texto del error
   (ya viene en `message["result"]["content"][0]["text"]`) y guardarlo
   como un atributo nuevo del `MCPError` (ej. `tool_error_text`) - no
   cambia `kind="tool"` ni ningún otro campo existente, solo agrega uno.
2. `core.py` (`_mcp_failure_error`): antes de caer al mensaje genérico
   para escrituras, revisar si `tool_error_text` contiene la frase
   estable `"row-level security policy"` (insensible a mayúsculas). Si
   sí: devolver `write_state = "not_sent"` (no "unknown") con un mensaje
   claro, por ejemplo:
   > "Supabase rechazó la escritura: las políticas de seguridad (RLS) no
   > permiten que esta cuenta escriba en esta tabla. No se guardó nada;
   > no hace falta reintentar. Si crees que deberías tener permiso,
   > contacta a un administrador."
   Si el texto NO coincide con ese patrón: se mantiene exactamente el
   comportamiento actual (mensaje genérico, `write_state = "unknown"`) -
   ningún otro caso cambia.
3. No se toca `tools.py`, ni la idempotencia (`ToolCallLog`,
   `_replay_or_execute`), ni ningún otro `write_state` existente. Solo se
   afina QUÉ mensaje y QUÉ `write_state` se asigna en este único patrón
   detectable, dejando todo lo demás byte-idéntico.

### Riesgo considerado y descartado

Capturar texto de error "crudo" de Postgres y mostrarlo podría, en teoría,
filtrar más detalle del que queremos (algunos errores de Postgres incluyen
valores de datos, ej. violación de restricción única). Por eso el arreglo
NO expone el texto crudo al modelo en general - solo lo usa internamente
para decidir la clasificación, y solo cuando coincide exactamente con el
patrón conocido y seguro de RLS. Para cualquier otro texto de error, se
descarta igual que hoy y se usa el mensaje genérico de siempre.

## Caso 2: por qué "0 registros" puede ser engañoso (y qué sí se puede hacer)

### Causa raíz exacta

A diferencia de una escritura, una lectura (`SELECT`) bajo RLS **no
genera ningún error** cuando las políticas ocultan filas - Postgres
simplemente las excluye del resultado, como si no existieran. Esto es el
comportamiento correcto y esperado de RLS, no un bug: desde el punto de
vista de esa cuenta, esas filas genuinamente no existen. No hay ninguna
señal técnica que distinga "la tabla está vacía" de "hay filas, pero RLS
las oculta a este usuario" - ambas producen exactamente la misma
respuesta (`0` filas). No es algo que el código pueda detectar y corregir
con más precisión; es una limitación inherente de cómo funciona RLS.

### El arreglo (solo texto, cero cambio de comportamiento)

Lo único que se puede mejorar es la information que tiene el MODELO para
redactar su respuesta - el texto del chat lo compone el modelo a partir
de la descripción de la herramienta y el resultado, no un mensaje fijo
nuestro. Hoy `leer_registros` (en `tool_catalogue.py`) no advierte de esta
ambigüedad. Se le agrega una frase a su `description`:

> "En modo rls_personal, un resultado con 0 filas puede significar que la
> tabla está vacía O que las políticas de RLS ocultan las filas a esta
> cuenta - ambos casos son indistinguibles. No afirmes que la tabla está
> vacía; indica que no se encontraron registros visibles para esta
> cuenta."

Esto no cambia ningún dato ni ninguna validación - es una línea de texto
más en una descripción que el modelo ya lee. Mismo riesgo que cualquier
otro cambio de redacción: ninguno, no hay lógica ejecutable de por medio.

## Fase adicional (ya la habíamos acordado): agregar `upsertar_registros`

Mismo patrón exacto que `insertar_registros`, en los mismos 3 archivos,
sin tocar `tools.py` ni `routes.py` (confirmado: `tools.py` despacha
genéricamente por nombre de tool, vía el conjunto `DENIABLE_TOOLS` - no
hay lógica por-tool ahí):

1. `config.py` - agregar `"upsertar_registros"` al frozenset
   `DENIABLE_TOOLS`.
2. `tool_catalogue.py` - copiar el bloque de `insertar_registros`
   (mismo `tabla`/`datos`, misma condición `config.allows(...)`), cambiar
   nombre/descripción, llamar a `supabase_upsertar`.
3. `core.py` (`_arguments`) - extender la rama que ya maneja
   `insertar_registros`/`actualizar_registros` para incluir
   `upsertar_registros`, reusando exactamente la misma validación de
   columnas escribibles que ya usa insertar (`_validated_data(...,
   insert=True)`), devolviendo `"supabase_upsertar"` como tool real.

Sin nuevo concepto de permisos - usa el allowlist de columnas
escribibles que ya existe para insertar/actualizar.

## Qué NO cambia con nada de esto

- Ninguna validación de allowlist (tablas/columnas) se modifica.
- Ninguna ruta de idempotencia o journaling de escrituras se modifica.
- `leer_registros`/`actualizar_registros`/`eliminar_registros` se
  comportan exactamente igual que hoy en todos los casos que no sean
  específicamente "escritura rechazada por RLS" (que pasa de un mensaje
  genérico a uno más claro, sin cambiar si la operación tuvo éxito o no -
  seguía fallando antes, sigue fallando ahora, solo que se explica mejor).
- No se toca `routes.py`, `integration.py`, `wai_identity.py`,
  `response_shape.py`, ni el frontend.

## Plan de pruebas antes de publicar

1. Pruebas nuevas en `tests/test_adapter_smoke.py`:
   - `upsertar_registros`: caso exitoso (reusa fixtures de
     `insertar_registros`), caso de columna no escribible rechazada.
   - Rechazo de escritura por RLS: un transporte simulado que devuelva
     `isError: true` con el texto exacto de Postgres
     ("new row violates row-level security policy...") debe producir
     `write_state == "not_sent"` y el mensaje claro nuevo - no el
     genérico.
   - Rechazo de escritura por otra causa (texto de error distinto, o
     sin texto): debe seguir devolviendo exactamente el mensaje y
     `write_state` genéricos de hoy - prueba de regresión para
     confirmar que NO se rompió el caso ambiguo real.
2. Correr toda la suite existente (13 pruebas) y confirmar que sigue en
   verde sin modificar ninguna de las ya existentes.
3. Probar en workspace B (el empleado real) los dos escenarios que tú
   mismo reportaste - confirmar que el chat ahora dice algo como "no
   tienes permiso" en vez de "no puedo confirmar", y que la lectura con
   RLS trae la aclaración en vez de sonar a tabla vacía.
4. Solo después de ver eso funcionar en B: sincronizar al skill, subir
   `VERSION`, y publicar.

## Resumen para decidir

Tres cambios, dos de ellos de texto puro (riesgo cero) y uno de código
acotado y con prueba de regresión explícita para el caso que NO debe
cambiar. Nada de esto toca la lógica de permisos, de idempotencia, ni el
comportamiento de ningún caso que hoy funciona bien.
