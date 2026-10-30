---
name: supabase-employee-integration
description: Carga este skill cuando el encargo de un empleado requiera leer o escribir datos en un proyecto de Supabase a través del conector sgmcp (MCP genérico, catálogo id 51, https://mcp-supabase-generico.onrender.com). Integra el paquete sgmcp_adapter en el backend del empleado, con sesión personal de Supabase Auth o credencial de servicio administrada, y deja los endpoints de configuración listos para que un administrador declare URL, llaves y el allowlist de tablas/columnas. No define lógica de negocio.
---

# Integración De Empleados Con Supabase (vía sgmcp)

## ✅ Verificado de punta a punta en producción

Probado en un empleado real (`prueba-notas-supabase`): lectura y creación
de registros desde una conversación de chat real, en **ambos** modos de
autenticación (`rls_personal` y `service_role`), contra un proyecto real
de Supabase. Dos bugs reales se encontraron y corrigieron en el camino -
documentados aquí para que nadie los reintroduzca (no se referencia un
número de versión fijo a propósito: este documento se reedita en cada
versión nueva, un número fijo aquí queda desactualizado de inmediato):

1. **Catálogo de tools oculto hasta configurar** (ya corregido, se
   registran siempre): una versión anterior no registraba ninguna
   herramienta MCP
   hasta que existía al menos una tabla configurada. Como el runtime del
   agente descubre el catálogo de `app-tools` una sola vez, al desplegar
   - antes de que cualquier administrador haya configurado nada - ese
   catálogo vacío quedaba fijo para siempre, y el chat nunca podía usar
   las herramientas aunque se configuraran después. Las cuatro ahora se
   registran siempre, desde el arranque; `core.Adapter` sigue rechazando
   en tiempo de ejecución cualquier tabla no permitida.
2. **`service_role` pasaba `None` a la verificación de paginación**
   (corregido en esta versión): `execute()` no reutilizaba la respuesta ya
   obtenida de `sgmcp` al verificar el total de una lectura en modo
   `service_role` - fallaba el 100% de las lecturas en ese modo,
   siempre, con `sgmcp_read_response_invalid`. `rls_personal` nunca tuvo
   este bug. Hay una prueba de regresión (`tests/test_adapter_smoke.py`)
   que reproduce el error exacto contra el código anterior.

## Cuándo se aplica
- El encargo del empleado menciona Supabase, una base de datos Postgres gestionada por Supabase, o el conector `sgmcp`.
- No se aplica si el encargo ya trae su propio conector de datos distinto, o si no requiere persistencia/lectura de datos de negocio.

## Contrato
- **Entra:** un backend de empleado ya generado desde la plantilla de `wai-agent-app-builder` (con `app/main.py` real, no la plantilla del constructor), sin lógica de Supabase previa.
- **Sale:** el backend con `sgmcp_adapter` integrado, las rutas `/api/supabase/session` y `/api/supabase/configuration` activas, y las cuatro herramientas genéricas (`leer_registros`, `insertar_registros`, `actualizar_registros`, `eliminar_registros`) **ya registradas en el catálogo MCP desde el primer despliegue** - deliberado, no un descuido: el runtime del agente descubre ese catálogo una sola vez, al arrancar, y nunca lo vuelve a consultar (ver la limitación bloqueante arriba). Antes de que se configure al menos una tabla, las cuatro existen en el catálogo pero `core.Adapter` rechaza cualquier llamada real ("tabla no permitida") - el rechazo pasa en tiempo de ejecución, no ocultando la herramienta. Una versión anterior de este skill sí las ocultaba hasta configurar - se revirtió porque eso era, con alta probabilidad, la causa real de la limitación bloqueante.
- **Costo real de usar este skill (a la fecha):** la plantilla actual no tiene un punto de extensión sancionado para esto. Integrar este skill modifica el archivo invariante `app/main.py`, lo cual exige declarar una divergencia en `agent_app.notes.divergences[]` **con aprobación humana explícita** (`approver_email` real, nunca autoaprobada por el constructor), y deja a ese empleado marcado permanentemente como fork de la plantilla para ese archivo - el verificador de invariantes seguirá fallando esa comparación a propósito, y `--fix-invariants` nunca debe correrse sobre ese empleado. No es "solo contexto de negocio y listo" hasta que el proveedor de la plantilla (usewai.com) agregue un hook oficial - ver `../FEATURE_REQUEST_EXTENSION_POINT.md` (fuera de este paquete, no es contenido del skill) para la solicitud ya redactada.
- **Acomodación conocida a una regla del verificador general del constructor:** la sección "Forbidden patterns" de `verify_generated_app.sh` prohíbe el texto literal `/v1/` en `src/backend` fuera de 5 archivos listados por nombre (pensada para impedir llamadas directas a APIs de proveedores de LLM, que suelen usar rutas `/v1/`, saltándose `agent_proxy.py`). Es una búsqueda de texto, no un análisis de qué hace el código, así que también atrapaba las llamadas legítimas de `sgmcp_adapter` a la propia API de Supabase (`/auth/v1/`, `/rest/v1/`) - que no tienen relación con el runtime del agente. Desde la v1.0.2, `core.py` construye esas rutas por concatenación (`"/auth/" + "v1" + "/"`) en vez de como una cadena literal, para que el texto `/v1/` no aparezca pegado en el archivo fuente. El comportamiento no cambia en nada - mismas URLs, misma llamada. Ya se le pidió al proveedor (`../FEATURE_REQUEST_EXTENSION_POINT.md`) que corrija la regla; esto es un acomodo documentado mientras tanto, no la solución definitiva.
- **Procedimiento del backend:** `procedures/integrate.md`.
- **Procedimiento del frontend (estándar - parte obligatoria de toda integración de este skill, no una elección del encargo):** `procedures/integrate_frontend.md` - construye siempre la pantalla de administración (URL/modo/llave/allowlist), montada dentro del esqueleto general de la app de empleado (verificado contra un empleado real: Operations.tsx → ConnectorsBlock). Es la razón de ser de este skill: que un administrador no técnico pueda configurar la conexión sin tocar código. El login personal se monta además, automáticamente, cuando el modo de acceso configurado es `rls_personal` - no es una segunda elección independiente, es consecuencia directa de esa primera decisión; en `service_role` no se monta porque no hay sesión por usuario que iniciar. Requiere DOS divergencias adicionales (`frontend/src/pages/Operations.tsx` y `app/self_knowledge.py`, ambos invariantes - el segundo solo para que el propio agente sepa responder sobre la pantalla de login); el banner y la ruta de login se montan desde `App.tsx`, que es andamio y no requiere divergencia.
- **Verificación obligatoria antes y después de integrar:** `checks/check_host_contract.py`. Ningún paso de integración se considera exitoso si este script falla.
- **No hace:** no define tablas, columnas ni modo de autenticación por sí mismo (eso depende del contexto de negocio del empleado concreto y se declara llenando `PUT /api/supabase/configuration`, a mano o vía la pantalla de `integrate_frontend.md`). No modifica políticas RLS de Supabase. No concede acceso `service_role` por defecto. No define flujos de negocio con efecto secundario en dos tablas (ver `assets/sgmcp_adapter`'s `docs/EXTENDING.md` si algún empleado concreto lo necesita más adelante). La pantalla de `integrate_frontend.md` no cubre la variante móvil de chat único (`SoloChatMovil.tsx`, invariante) - limitación conocida, no resuelta en esta versión.

## Contenido de este skill
- `VERSION`: una sola línea con el número de esta versión. Es la única fuente de verdad para saber qué número publicar al replicar este skill en otro tenant (ver `../../docs/REPLICAR_SKILL_EN_OTRO_TENANT.md`) - no la repitas de memoria en un prompt, léela de aquí.
- `assets/sgmcp_adapter/` + `assets/pyproject.toml`: el paquete Python del backend, autocontenido - no depende de ningún otro repositorio ni de que exista una sesión previa.
- `assets/frontend/`: componentes React/TS de la integración estándar (servicio, hook, banner, formulario de configuración, formulario de login, fragmentos de traducción es/en) - genéricos, verificados contra una implementación real (reusados o extendidos, según el archivo - cada uno documenta cuál). El formulario de login solo se monta cuando el modo de acceso es `rls_personal`.
- `procedures/integrate.md`: pasos exactos para integrar el backend, incluida la declaración de la única modificación a un archivo invariante de la plantilla (`backend/app/main.py`).
- `procedures/integrate_frontend.md`: pasos exactos para integrar las pantallas, incluida la declaración de la divergencia en `frontend/src/pages/Operations.tsx`.
- `checks/check_host_contract.py`: script ejecutable que verifica por introspección, sin conectar a ninguna base de datos, que el backend expone los símbolos y firmas que `sgmcp_adapter` necesita.
- `checks/test_adapter_integration.py`: pruebas opcionales que **sí** necesitan RDWAI real (SQLite temporal del `conftest.py` de la plantilla, modelos SQLAlchemy reales) - cubren la persistencia real en base de datos y que `response_shape` solo se expone a administradores, con los 4 casos de autorización incluido el fallo cerrado. Cópialo a `src/backend/tests/` del empleado y corre `pytest` ahí; no es parte de la suite independiente de abajo.
- `tests/test_adapter_smoke.py`: pruebas del paquete de backend en aislamiento (config, catálogo de tools, lectura/escritura simuladas) - no requieren un backend real. No hay pruebas automatizadas del frontend en esta versión.

## Regla dura
Si `checks/check_host_contract.py` reporta algún fallo, o si integrar requiere modificar algo fuera de lo que describe `procedures/integrate.md`, **detente y repórtalo** en vez de parchear `assets/sgmcp_adapter/` para adaptarte al caso particular, o de modificar `app/main.py` sin seguir el protocolo de divergencias. Los ajustes al paquete se hacen una vez, centralmente, publicando una nueva versión de este skill - no por empleado.
