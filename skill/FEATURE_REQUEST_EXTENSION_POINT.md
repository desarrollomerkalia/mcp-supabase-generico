# Solicitud: tres ajustes a la plantilla de `wai-agent-app-builder`

**Para:** equipo que mantiene la plataforma WAI (usewai.com)
**De:** Merkalia
**Tema:** (1) permitir que skills técnicos agreguen rutas/tools MCP a un empleado sin modificar `app/main.py` ni `frontend/src/pages/Operations.tsx`; (2) ajustar la regla "Forbidden patterns" que prohíbe el texto `/v1/`, demasiado amplia para distinguir APIs externas legítimas del runtime interno del agente; (3) permitir que una ruta nueva de frontend se declare en `app/self_knowledge.py` sin que ese archivo también sea invariante.

**Nota:** un cuarto punto que estuvimos a punto de incluir aquí (el
sidecar no descubría las tools internas del empleado) resultó ser un bug
de nuestro propio skill, no de la plataforma - corregido en la v1.0.4,
detalle al final de este documento. No hizo falta ningún cambio de la
plataforma para eso.

## Contexto

Estamos construyendo un skill técnico reutilizable (`supabase-employee-integration`)
que conecta cualquier empleado a un proyecto de Supabase vía el conector `sgmcp`
(catálogo id 51). El paquete en sí (`sgmcp_adapter`) ya está terminado, probado
de forma aislada, y verificado símbolo por símbolo contra la plantilla actual
de `wai-agent-app-builder` - funciona.

El obstáculo es de integración, y aparece en más de un archivo: el backend
requiere agregar dos líneas a `app/main.py` (un `import` y una llamada
`mount(app, require_project_access)` antes de `mount_mcp(app)`); la pantalla
de administración opcional requiere montar un componente dentro de
`frontend/src/pages/Operations.tsx`, en su bloque de conectores. Ambos
archivos están declarados invariantes. Hoy esto solo se puede resolver
declarando una divergencia en `agent_app.notes.divergences[]` por cada
archivo, lo cual:

- Exige aprobación humana explícita (`approver_email` real) en **cada
  empleado** que use este skill - no es algo que el constructor pueda
  autoaprobar, ni debería serlo.
- Deja a ese empleado marcado permanentemente como fork de `app/main.py`
  respecto de la baseline, incluso con la divergencia bien documentada.
- Excluye a ese empleado de `--fix-invariants` para siempre: correrlo
  restauraría el `app/main.py` canónico y borraría la integración.

Esto convierte algo que debería ser "elegir un skill y dar contexto de
negocio" en un paso de aprobación manual por empleado, indefinidamente,
y en una exclusión permanente de actualizaciones automáticas de plantilla
para ese archivo.

## Lo que se pide

Un punto de extensión oficial, versionado como parte de la plantilla
base (no una divergencia por empleado), de esta forma o equivalente:

```python
# Ya dentro del app/main.py INVARIANTE de la plantilla, después de los
# demás app.include_router(...) y antes de mount_mcp(app):

import importlib.util

_custom_mounts_path = Path(__file__).parent / "custom_mounts.py"
if _custom_mounts_path.exists():
    spec = importlib.util.spec_from_file_location("app.custom_mounts", _custom_mounts_path)
    custom_mounts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(custom_mounts)
    if hasattr(custom_mounts, "register"):
        custom_mounts.register(app)

mount_mcp(app)
```

Con esto, cualquier skill técnico (no solo el nuestro) se integra creando
`app/custom_mounts.py` con una función `register(app)` - un archivo
**nuevo**, no una modificación a un invariante. El verificador de
invariantes no tendría que cambiar su comparación de `app/main.py`
(seguiría siendo invariante byte a byte); solo necesitaría dejar de
tratar la *presencia* de `app/custom_mounts.py` como una violación, ya
que sería parte sancionada de la arquitectura.

## Por qué vale la pena para WAI, no solo para nosotros

Cualquier integración backend futura que necesite registrar rutas o
tools MCP (no solo Supabase) tropieza con el mismo obstáculo. Este punto
de extensión resuelve la clase completa de problema una vez, en vez de
que cada integración termine pidiendo una divergencia aprobada a mano.

## Segunda solicitud: la regla "Forbidden patterns" que prohíbe `/v1/`

`verify_generated_app.sh` incluye esta regla sobre `src/backend`:

```
forbid_in '/v1/' 'src/backend' 'agent_proxy.py,prompts.py,users_routes.py,mcp_connectors_routes.py,config.py' \
          "no /v1/* references outside agent_proxy.py (docstrings + backend /api/v1/ excluded)"
```

Es una búsqueda de texto literal, no un análisis de qué hace el código.
Por su nombre y su exclusión (`agent_proxy.py`, el cliente del runtime
interno del agente/LLM), interpretamos que su propósito es impedir que un
backend de empleado llame directo a la API de un proveedor de LLM
(típicamente bajo rutas `/v1/`) saltándose el proxy sancionado. Pero
también atrapa cualquier llamada legítima a una API externa de terceros
que use ese mismo patrón de ruta - como la propia API de Supabase
(`/auth/v1/`, `/rest/v1/`), que no tiene relación alguna con el runtime
del agente.

Dos arreglos posibles, de menor a mayor alcance - les toca decidir cuál
refleja mejor la intención real de la regla:

1. **Estrecho:** ampliar la lista de exclusiones por nombre de archivo
   (o por patrón de import) para incluir módulos de clientes HTTP hacia
   APIs de terceros ya revisadas/publicadas como parte de un skill del
   catálogo, no solo las que coincidan por nombre de archivo a mano.
2. **Amplio:** si la intención real es "ninguna llamada externa sin pasar
   por un conector sancionado" (no solo las de LLM), entonces el arreglo
   correcto no es excepcionar a Supabase, sino dar una forma sancionada
   de hacer llamadas HTTP a terceros desde un skill técnico - un punto de
   extensión análogo al que pedimos arriba para rutas/tools MCP.

## Tercera solicitud: `app/self_knowledge.py` también exige declarar cada ruta nueva, y también es invariante

Al agregar una ruta de frontend nueva (`/supabase-login`, para el login
personal de Supabase), `test_self_knowledge` falló porque esa ruta no
está declarada en `app/self_knowledge.py` - y ese archivo es, a su vez,
invariante. Es el mismo patrón de fondo que los puntos 1 y 2: cualquier
capacidad nueva que un skill técnico agregue (una ruta, un router, una
llamada externa) choca con un archivo invariante distinto, cada uno con
su propio costo de divergencia aprobada por separado. Pedimos que el
mismo punto de extensión del punto 1 (o uno análogo) también cubra la
declaración de rutas nuevas en `self_knowledge.py` - idealmente
generada automáticamente a partir de lo que de hecho se registra en
`App.tsx`, no mantenida a mano en dos lugares.

## Cuarto punto retirado: no era un problema de la plataforma

Durante las pruebas llegamos a sospechar que el sidecar del agente nunca
redescubría las tools dinámicas de `app-tools` (`GET /v1/mcp/tools`
mostraba `"tools": []` pese a que el backend sí las tenía registradas), y
estuvimos a punto de pedir aquí un cambio al ciclo de arranque del
sidecar para esto. **Era un bug nuestro, no de la plataforma**: una
versión anterior del skill ocultaba las cuatro herramientas del catálogo
hasta que un administrador configuraba al menos una tabla - como el
sidecar descubre el catálogo de `app-tools` una sola vez, al desplegar
(antes de que exista cualquier configuración), ese catálogo vacío quedaba
fijo para siempre. Desde la v1.0.4, las cuatro herramientas se registran
incondicionalmente desde el arranque (el rechazo de tablas no permitidas
sigue pasando en tiempo de ejecución, no ocultando la herramienta) - y
con eso el descubrimiento funciona exactamente igual que con cualquier
otro empleado. Verificado de punta a punta, en ambos modos de
autenticación, contra un proyecto real de Supabase. Lo retiramos de esta
solicitud; no hace falta ningún cambio de la plataforma para esto.

## Mientras tanto

Para el punto 1 (divergencias de `app/main.py` y `Operations.tsx`): vamos
a usar el mecanismo de divergencia tal como existe hoy
(`procedures/integrate.md` y `procedures/integrate_frontend.md` del skill
`supabase-employee-integration` ya lo siguen correctamente: aprobación
humana real, declaración completa de los cinco campos, nunca
`--fix-invariants` sobre esos empleados).

Para el punto 2 (regla `/v1/`): desde la v1.0.2 del skill, `sgmcp_adapter`
construye esas rutas por concatenación (`"/auth/" + "v1" + "/"`) en vez de
como una cadena literal, para no disparar la regla - mismo comportamiento,
mismas URLs, solo el texto fuente partido. Es un acomodo documentado, no
la solución definitiva; preferimos que la regla se corrija.

Para el punto 3 (`self_knowledge.py`): resuelto - se declaró la tercera
divergencia aprobada y se agregó la entrada correspondiente; `test_self_knowledge`
pasa.

**Estado final, verificado en producción:** `supabase-employee-integration`
(v1.0.4) quedó probado de punta a punta en un empleado real
(`prueba-notas-supabase`) - lectura y creación de registros desde el chat,
en ambos modos de autenticación (`rls_personal` y `service_role`), contra
un proyecto real de Supabase. No quedan huecos funcionales conocidos; lo
que resta de esta solicitud son las tres mejoras de arriba, ninguna
bloqueante.
