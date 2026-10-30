# Prompt único de integración

Copia y pega esto en el chat del agente de RDWAI **después** de cargar la
carpeta `sgmcp_adapter/` (y `pyproject.toml`) a los ficheros del proyecto,
vía "Cargar código". No hace falta nada más antes de pegarlo - este
prompt ya trae todo lo que se verificó a mano una vez contra la plantilla
de `wai-agent-app-builder`, incluido un autochequeo: si algo no coincide,
el agente debe detenerse y avisar en vez de adivinar.

```
Ya tengo cargada en los ficheros de este proyecto la carpeta sgmcp_adapter/
(paquete Python) y su pyproject.toml. Es un adaptador genérico para conectar
este empleado a un proyecto de Supabase a través del conector MCP compartido
en https://mcp-supabase-generico.onrender.com. Intégralo siguiendo EXACTAMENTE
estos pasos, sin pedirme confirmación intermedia salvo que el paso 2 encuentre
una diferencia:

1. Agrega el paquete sgmcp_adapter (y sus dependencias httpx, fastapi, pydantic
   si no están ya en el proyecto) al entorno del backend de este empleado.

2. ANTES de tocar nada más, verifica que existen exactamente estos símbolos en
   el backend, con estos mismos nombres y módulos. Si alguno falta, se llama
   distinto, o tiene una firma distinta, DETENTE y dime cuál - no continúes ni
   modifiques sgmcp_adapter para adaptarte a la diferencia:
   - app.auth.require_project_access: dependencia FastAPI que entrega un
     objeto con atributo .email (string).
   - app.app_database.app_session: sessionmaker async, se usa como
     "async with app_session() as session". app.app_database.app_write:
     gestor de contexto async equivalente para escritura.
   - app.app_models.runtime.AgentRun con columnas id, run_token, mode, status,
     finished_at, request_meta_json.
   - app.app_models.runtime.AppSetting con columnas key (primary key), value,
     updated_by.
   - app.app_models.runtime.ToolCallLog con columnas run_id, step_idx,
     tool_name, idempotency_key (unique), result_hash, result_json.
   - app.channels.encryption.encrypt_blob(dict) -> str,
     decrypt_blob(str) -> dict, encryption_available() -> bool.
   - app.mcp_server.TOOLS y app.mcp_server.MCP_TOOL_SCHEMAS: diccionarios que
     se puedan mutar por clave (no reemplazar por un objeto nuevo).
   - app.mcp_actor.LEVEL_HUMAN y app.mcp_actor.TOOL_MIN_ROLE: diccionario
     mutable por clave.
   - app.autonomous.tool_idempotency._hash y
     _replay_or_execute(session, *, run_id, step_idx, tool_name, args,
     do_work, mode="autonomous") -> tuple.
   - app.users_directory.is_admin(email) -> bool, asíncrona.
   - Un archivo (normalmente app/main.py) donde se cree la instancia FastAPI
     (app = FastAPI(...)), se registren routers con app.include_router(...), y
     se monte el MCP interno del empleado (una llamada tipo mount_mcp(app)).
   - El objeto que reciben las funciones registradas en mcp_server.TOOLS[nombre]
     debe exponer .run_id (int), .step_idx (int), .name (str) y .arguments
     (dict).

3. Si todo coincide: en el archivo de composición de la app (app/main.py o
   equivalente), agrega junto a los demás imports:
   from app.auth import require_project_access
   from sgmcp_adapter.integration import mount

   Y agrega esta línea inmediatamente antes de la llamada que monta el MCP
   interno del empleado, después de los demás app.include_router(...):
   mount(app, require_project_access)

   No cambies nada más de ese archivo. No modifiques ningún archivo dentro de
   sgmcp_adapter/ - si algo no encaja, es una señal de que el paso 2 debió
   detenerse, no una invitación a parchear el paquete.

4. Arranca (o compila/importa) el backend y confirma que no hay errores de
   importación ni de arranque.

5. Haz una petición GET /api/supabase/configuration autenticada como
   administrador de este empleado. Debe devolver JSON con configured: false
   (o un código de error claro de "falta configuración") - nunca un 404 ni un
   500.

6. NO llenes todavía PUT /api/supabase/configuration ni definas tablas o
   columnas. En esta fase solo quiero la integración genérica montada y
   verificada, sin contexto de negocio todavía.

Al final repórtame: (a) si el paso 2 encontró alguna diferencia de nombres o
firmas, con el detalle exacto, (b) la respuesta exacta del paso 5, (c)
cualquier error de arranque o importación que haya aparecido.
```

## Si el paso 2 reporta una diferencia

No improvises sobre la marcha. Trae la diferencia exacta de vuelta a esta
conversación (o a quien mantenga `sgmcp_adapter`) - casi siempre es un
cambio de nombre o firma en una sola función, y el ajuste correcto se hace
una vez en el paquete genérico, no cada vez que alguien construye un
empleado nuevo.
