# sgmcp-adapter-generic

Adaptador genérico de sesión/escritura para conectar un empleado de RDWAI
a un proyecto de Supabase a través del conector `supabase-generic-mcp`.

Es la versión reutilizable del adaptador que se construyó la primera vez
para "Mara" (un empleado de renta de habitaciones de motel). Todo lo que
en esa primera versión estaba escrito a mano para ese negocio específico
(qué tablas, qué columnas, qué reglas de validación) aquí es
**configuración**, no código. Lo que sí seguía siendo genérico - sesión
de Supabase Auth por persona, journal de idempotencia, verificación de
totales de paginación, redacción de secretos - se mantiene sin cambios de
comportamiento.

## Flujo para construir un empleado nuevo

1. **Elige el conector `supabase-generic-mcp`** al construir el empleado en RDWAI.
2. **Carga esta carpeta (`sgmcp_adapter/` + `pyproject.toml`)** en los ficheros del proyecto (botón "Cargar código"), y **pega el prompt de `docs/SETUP_PROMPT.md`** tal cual en el chat del agente. Ese prompt ya trae integrado todo lo que se verificó a mano una vez contra la plantilla de `wai-agent-app-builder` (nombres de módulos, dependencia de auth correcta, orden de montaje) y un autochequeo: si algo no coincide con la plantilla real de este backend, el agente se detiene y avisa en vez de adivinar. No hace falta repetir la verificación manual por cada empleado nuevo.
3. **Dale contexto de negocio al agente que construye al empleado** (qué tablas, qué columnas, qué puede escribir, qué modo de autenticación). Ese contexto se traduce al JSON de configuración que se llena en el paso 4 - ver "Cómo traducir una descripción de negocio a configuración".
4. **Llena los campos de configuración** vía `PUT /api/supabase/configuration` (ver "Configuración"): URL del proyecto, `anon_key`/`service_role_key` según el modo, y el allowlist de tablas/columnas. A partir de aquí el empleado ya sabe hablar con Supabase - no hay una quinta conexión que configurar.

## Qué SÍ es genérico aquí (no se reescribe por empleado)

- **Sesión de Supabase Auth por persona** (`core.py`): login, verificación contra el endpoint `user` de la API de Auth de Supabase, renovación, cierre de sesión. Cada persona usa su propia cuenta; el adaptador nunca infiere un rol admin por el correo.
- **Journal de idempotencia** (`tools.py`): a lo más un envío externo por combinación de turno/paso/herramienta/argumentos. Una escritura nunca se reintenta si no se puede probar que no se envió.
- **Verificación de totales de paginación** (`core.py: _validated_page_total`): nunca confía en un solo conteo reportado por sgmcp; lo verifica con sondeos antes de decir "lectura completa".
- **Redacción de secretos**: el JWT de la persona, el anon_key y el service_role_key nunca llegan al modelo, ni siquiera dentro de un mensaje de error.
- **Cliente MCP** (`mcp_client.py`): habla el protocolo streamable HTTP contra el MCP genérico compartido, con límites de tamaño y validación estricta del framing.

## Qué se configura por empleado (no se escribe en Python)

Todo vive en `ConnectionConfig` (`config.py`), como JSON:

```json
{
  "auth_mode": "rls_personal",
  "tables": {
    "rooms": {
      "columns": {
        "id": {"type": "string"},
        "status": {"type": "string", "enum": ["busy", "available", "cleaning"]}
      },
      "writable": ["status"],
      "filterable": ["id", "status"]
    }
  },
  "denied": ["eliminar_registros"],
  "read_limit": 10000
}
```

- `auth_mode`: `rls_personal` (cada persona inicia sesión con su propia cuenta de Supabase; RLS decide qué ve) o `service_role` (una sola credencial administrada, sin login individual - ver "Los dos modos de autenticación").
- `tables`: qué tablas existen para este empleado, qué columnas puede leer, cuáles puede escribir (`writable`) y por cuáles puede filtrar (`filterable`). Las reglas por columna (`type`, `enum`, `pattern`, `min_length`, `max_length`, `required`) son lo que reemplaza la validación de negocio que antes estaba a mano (duración en horas, tipo de vehículo, método de pago, etc. en el caso de Mara).
- `denied`: cuáles de `insertar_registros`/`actualizar_registros`/`eliminar_registros` **no** se exponen a este empleado. `leer_registros` no se deniega por aquí - si no quieres que lea algo, no lo declares en `tables`.

## Los dos modos de autenticación

| | `rls_personal` | `service_role` |
|---|---|---|
| Quién tiene cuenta en Supabase | Cada usuario final | Nadie; una sola credencial |
| Qué viaja en cada llamada al MCP | `anon_key` + JWT personal | `service_role_key` |
| Quién protege los datos | Postgres RLS | Solo el allowlist de `tables`/`denied` de este archivo |
| Cuándo usarlo | El negocio ya tiene Auth de Supabase por persona (como Mara con el personal del motel) | No existe el concepto de "cuenta propia del usuario final" en Supabase |

En `service_role`, el allowlist de tablas/columnas **es la única barrera** -
no hay RLS de respaldo. Decláralo con más cuidado en ese modo.

El `service_role_key` nunca se guarda como variable de entorno del MCP: el
MCP genérico es un proceso compartido entre todos los empleados (ver nota
al final), así que la credencial vive cifrada en el almacén de
configuración de este empleado y se inyecta en cada llamada, igual que ya
pasa con el JWT personal en modo `rls_personal`.

## Cómo traducir una descripción de negocio a configuración

Cuando el agente que construye el empleado recibe algo como "necesito que
las rentas tengan una duración en horas, un tipo de vehículo (coche,
moto, a pie) y un método de pago (efectivo o tarjeta)", eso se traduce
directo a reglas de columna:

```json
"service_type": {"type": "string", "pattern": "^[0-9]+(\\.[0-9]+)? (hora|horas)$"},
"type": {"type": "string", "enum": ["car", "bike", "foot"]},
"payment_method": {"type": "string", "enum": ["efectivo", "tarjeta"]}
```

No se escribe una función de validación nueva por cada campo: `type`,
`enum`, `pattern`, `min_length`/`max_length` y `required` en `ColumnRule`
(`config.py`) cubren la gran mayoría de reglas de negocio expresables como
"debe ser uno de estos valores" / "debe tener este formato".

## Lo que NO se generaliza gratis

Un flujo con efecto secundario en dos tablas (como el alta de una renta
que además marca una habitación como ocupada) sigue siendo lógica de
negocio propia, no configuración. Este paquete trae las cuatro
operaciones CRUD genéricas con journal de idempotencia incluido; si un
empleado necesita un flujo de ese tipo, sigue el patrón descrito en
`docs/EXTENDING.md` en vez de forzarlo dentro del allowlist.

## Integración

Para *hacer* la integración, usa el prompt de `docs/SETUP_PROMPT.md` (paso
2 del flujo de arriba) - no sigas esta sección a mano cada vez, es la
referencia técnica detrás de ese prompt, útil para depurar si algo falla.

Verificado contra la plantilla de `wai-agent-app-builder` (`app/main.py`,
`app/auth.py`, `app/mcp_server.py`): va en `app/main.py`, después de los
`app.include_router(...)` existentes y justo antes de `mount_mcp(app)`
(ese orden es solo por claridad - `mount_mcp` no inicializa ni reemplaza
los registros de tools, así que el orden exacto entre ambas líneas no es
estrictamente obligatorio):

```python
from app.auth import require_project_access
from sgmcp_adapter.integration import mount

adapter = mount(app, require_project_access)
# ... justo después, como ya existe en el archivo:
mount_mcp(app)
```

- `app`: la aplicación FastAPI del empleado.
- `require_project_access`: la dependencia correcta para rutas accesibles por cualquier persona con acceso autorizado a este empleado - NO uses `get_current_user` a secas (solo valida el JWT, no pertenencia al proyecto ni autorización en este empleado) ni `require_admin` (eso ya se pasa aparte como `admin_dependency`, internamente, para las rutas de solo-administrador). `require_project_access` entrega un `TokenPayload` con `.email` ya resuelto desde el claim `sub` del JWT - es lo que usan `routes.py`/`core.py` en todo el adaptador.
- `service_token` (opcional): solo tiene efecto si en algún momento agregas una verificación de secreto compartido al MCP genérico (ver "Nota sobre `server.js`" abajo). Hoy el MCP desplegado no la exige, así que se omite.

`register_tools()` (llamado dentro de `mount()`) muta por clave los
diccionarios `mcp_server.TOOLS` / `mcp_server.MCP_TOOL_SCHEMAS` /
`mcp_actor.TOOL_MIN_ROLE` que ya existen al importar esos módulos - nunca
los reemplaza por un objeto nuevo, como exige la plantilla.

Esto registra:
- `GET/POST/DELETE /api/supabase/session` - estado de sesión, login y cierre de sesión personal (solo aplica en `rls_personal`).
- `GET/PUT /api/supabase/configuration` - consulta y configuración, solo administradores del empleado.
- Las cuatro herramientas genéricas en el registro MCP interno del empleado (`leer_registros`, `insertar_registros`, `actualizar_registros`, `eliminar_registros`), ya filtradas por `denied`.

`mcp_routes.py` además expone un endpoint MCP independiente
(`POST /mcp` / `POST /mcp/supabase`), útil para probar el adaptador fuera
del flujo de chat real. **Las escrituras por ese endpoint no tienen el
journal de idempotencia** - es un adaptador de protocolo para pruebas, no
el cableado de producción. El cableado real es `tools.register_tools()`,
ya incluido en `mount()`.

## Limitaciones conocidas (dilas en voz alta, no las escondas)

- **Confirmado contra la plantilla real** (`app/routes/agent_routes.py`,
  `app/memory_utils.py`, `app/mcp_server.py:96-110,5638-5657`): el correo
  vive en `AgentRun.request_meta_json["memory_user"]`, y el objeto `call`
  que recibe un handler registrado en `mcp_server.TOOLS[name]` expone
  `.run_id`/`.step_idx`/`.name`/`.arguments` tal como se asumía. Ninguno
  de los dos puntos requirió cambios de código.
- **Condición de carrera real a tener presente (no es un bug, es cómo
  funciona la plataforma)**: `persist_run_memory_user` escribe
  `memory_user` en `request_meta_json` de mejor esfuerzo, posiblemente
  después de que el run ya existe, y esa escritura puede fallar sin
  bloquear el run. Si una tool de este adaptador se invoca muy al inicio
  de un turno de chat, antes de que esa escritura haya terminado,
  `active_chat_email` devolverá `None` y la persona verá
  `LoginRequired` aunque sí esté en un chat activo. No se puede arreglar
  desde este paquete - si lo ves en pruebas reales, es una señal de
  timing de la plataforma, no de que la identidad esté mal resuelta.
  También importante: `call.mode` vale `"autonomous"` incluso dentro de
  un run de chat - por eso el adaptador nunca lo lee y siempre confirma
  `AgentRun.mode == "chat"` directo en base de datos antes de confiar en
  una identidad.
- **Sesiones en memoria del proceso**: un reinicio del backend del
  empleado desconecta a todas las personas en modo `rls_personal`. No es
  multi-réplica: usa un solo proceso por empleado para esta versión, igual
  que la primera prueba con Mara.
- **Verificado contra un backend real de `wai-agent-app-builder`** (empleado
  de prueba desechable, sin lógica de negocio, modo `service_role`, tabla
  `notas`): contrato del host 15/15, pruebas aisladas 9/9, arranque limpio,
  `GET /api/supabase/configuration` y `GET /api/supabase/session` responden
  como se espera, cero herramientas MCP registradas antes de configurar
  (confirmado en los registros reales del backend, no solo en pruebas
  unitarias), y el verificador completo de la plantilla queda con solo los
  fallos esperados (el fork aprobado de `app/main.py` y los requisitos
  genéricos de "aplicación completa" ajenos a Supabase). **Lo que falta
  todavía: una prueba real de conexión a Supabase** - login/lectura/
  escritura contra un proyecto real, después de llenar `PUT /api/supabase/
  configuration` con credenciales reales. Hasta que eso se complete, lo
  verificado es "la integración técnica funciona", no "el ciclo completo
  con datos reales funciona".
- **Validación de columnas más simple que la original de Mara**: las
  reglas por columna (`type`/`enum`/`pattern`/longitud) cubren la mayoría
  de casos, pero un mensaje de error de validación aquí es un string, no
  una lista estructurada de `missing_fields`/`invalid_fields` como tenía
  el adaptador original. Es una simplificación deliberada por ahora.

## Cómo correr las pruebas incluidas

```bash
cd adapter
pip install -e ".[test]"
pytest
```

Estas pruebas **no** requieren RDWAI instalado - solo prueban
`config.py`/`core.py`/`tool_catalogue.py` de forma aislada, con un
transporte MCP falso. Confirman: validación de configuración, que una
`service_role_key` nunca puede colarse en el lugar de una `anon_key` (y
viceversa), generación del catálogo de herramientas según `denied`,
lectura con verificación de paginación, y que una escritura exige pasar
primero por `preflight_write`.

## Nota importante sobre el MCP genérico desplegado

Al revisar este paquete se encontró que el `server.js` de la raíz de este
repositorio (versión `2.2.0`) **no coincide** con lo que realmente corre
en `https://mcp-supabase-generico.onrender.com` (versión `2.4.0` al
momento de escribir esto, confirmado contra el servidor real). La versión
desplegada ya soporta `pagina`/`orden_columna`/`orden_asc`/conteo total en
`supabase_leer` - este adaptador depende de eso para la verificación de
paginación. Antes de volver a desplegar `server.js` desde este repo,
actualízalo para que coincida con lo que ya está en producción, o se
perderá esa funcionalidad.

También se recomienda agregar al `server.js` desplegado una verificación
de un secreto compartido (header `X-WAI-MCP-Key`, igual al patrón que ya
se usa para el conector `supabase-impersonacion`) antes de aceptar
`tools/call` - hoy cualquiera que alcance esa URL puede ejecutar
`supabase_ejecutar_sql`/`supabase_auth_listar_usuarios` si además tiene
una service_role key válida de algún proyecto. `MCPClient` en este
adaptador ya soporta mandar ese header (`service_header=` en
`mcp_client.MCPClient`, cableado desde `integration.mount(service_token=...)`)
en cuanto el servidor lo exija.
