# Publicar la versión 1.0.11 en los dos tenants

Todos los cambios recientes (upsert, mensaje claro de rechazo RLS,
diagnóstico admin-only, corrección del id de catálogo) se hicieron
probando directo en workspace B. Ya quedaron confirmados funcionando en
producción, pero **todavía no están publicados en el skill del catálogo**
- ni en el tenant de origen (workspace A) ni en el segundo tenant donde ya
replicamos la skill antes. Hay que publicarlos en los dos, por separado,
con el mismo número de versión en ambos.

## Archivos que cambiaron desde la 1.0.7 (la última publicada)

7 archivos, nada de frontend esta vez:

- `SKILL.md`
- `VERSION`
- `assets/sgmcp_adapter/config.py`
- `assets/sgmcp_adapter/core.py`
- `assets/sgmcp_adapter/mcp_client.py`
- `assets/sgmcp_adapter/tool_catalogue.py`
- `tests/test_adapter_smoke.py`

Todos viven en `skill/supabase-employee-integration/` dentro de este
repo - súbelos desde ahí a cada sesión.

## Paso 1 — Workspace A (el tenant de origen, donde vive la skill oficial)

Prompt:

```
Vas a publicar una nueva versión del skill "supabase-employee-integration".
La 1.0.7 es la última publicada aquí. Te adjunto 7 archivos actualizados:

- SKILL.md
- VERSION
- assets/sgmcp_adapter/config.py
- assets/sgmcp_adapter/core.py
- assets/sgmcp_adapter/mcp_client.py
- assets/sgmcp_adapter/tool_catalogue.py
- tests/test_adapter_smoke.py

Haz esto, en orden:
1. Lee VERSION - debe decir 1.0.11. Ese es el número exacto que vas a
   publicar, sin modificarlo.
2. Reemplaza unicamente esos 7 archivos dentro de la version actual del
   skill. No toques nada mas (procedures/, checks/,
   assets/sgmcp_adapter/integration.py, mcp_routes.py, response_shape.py,
   routes.py, wai_identity.py, __init__.py, assets/pyproject.toml, ni
   nada de assets/frontend/ - todos quedan exactamente igual que en la
   1.0.7).
3. Publica como 1.0.11.

Resumen de los cambios de fondo, por si te sirve de contexto: se agrego
una quinta tool generica (upsertar_registros), se corrigio la
clasificacion de errores de escritura (un rechazo por politica RLS de
Supabase ahora se reporta con mensaje claro en vez de uno generico - esto
necesito dos correcciones encadenadas en mcp_client.py, documentadas en
el changelog de SKILL.md), se agrego un diagnostico admin-only para
escrituras sin clasificar, y se corrigio una referencia desactualizada a
un id de catalogo especifico de tenant. El contrato externo (rutas,
nombres de las otras cuatro tools, idempotencia) no cambio.

Confirmame: el numero con el que quedo publicado, que el contenido de los
7 archivos coincide exactamente con lo que te entregue, y que el resto de
la 1.0.7 no cambio.
```

## Paso 2 — El segundo tenant (actualizar la versión ya existente ahí)

Mismos 7 archivos. Prompt:

```
Vas a actualizar la version del skill "supabase-employee-integration" que
ya esta publicado en este tenant (lo publicamos como 1.0.7 hace poco). No
es un skill nuevo - es una nueva version del mismo.

Te adjunto los mismos 7 archivos actualizados:
- SKILL.md
- VERSION
- assets/sgmcp_adapter/config.py
- assets/sgmcp_adapter/core.py
- assets/sgmcp_adapter/mcp_client.py
- assets/sgmcp_adapter/tool_catalogue.py
- tests/test_adapter_smoke.py

Haz esto:
1. Lee VERSION - debe decir 1.0.11. Usa exactamente ese numero al
   publicar - no reinicies la numeracion ni propongas otro, debe
   coincidir con el mismo numero que se publico en el tenant de origen,
   para que "1.0.11" signifique lo mismo en todos los tenants.
2. Reemplaza unicamente esos 7 archivos dentro de la version existente
   del skill aqui. No toques nada mas.
3. Publica como 1.0.11.

Nota: si al publicar ves scope: "orphaned" sin campo kind (ya lo vimos
antes en este tenant) - no es un problema, es cosmetico, no bloquea nada.
No pauses ni pidas correccion por eso; solo confirma al final que el
skill sigue apareciendo seleccionable en "Skills adicionales".

Confirmame el numero con el que quedo publicado y que coincide
exactamente con los 7 archivos que te entregue.
```

## Verificación final (en los dos tenants)

- El número publicado es 1.0.11 en ambos.
- `supabase-employee-integration` sigue apareciendo en "Skills
  adicionales" al abrir una sesión nueva de construcción de empleado.
- No se tocó nada fuera de los 7 archivos listados.
