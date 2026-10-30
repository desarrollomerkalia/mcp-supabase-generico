# Procedimiento De Integración

Sigue estos pasos en orden. No improvises un paso distinto porque "parece
que funciona" - si algo no encaja, detente en ese paso y repórtalo.

## 0. Precondición

Debe existir ya un backend de empleado generado de verdad (con
`app/main.py` real, no la plantilla del constructor). Si todavía no
existe, este procedimiento no aplica todavía: carga este skill desde el
inicio de la sesión igual, pero ejecuta su integración en cuanto el
backend generado exista - antes de dar por terminada la construcción del
empleado, no como un paso separado posterior que se pueda olvidar.

## 1. Instalar el paquete

Agrega `assets/sgmcp_adapter/` como módulo del backend (o instala
`assets/pyproject.toml` como dependencia editable, según el mecanismo de
dependencias que use este backend en particular). Agrega `httpx`,
`fastapi` y `pydantic` si no están ya presentes.

## 2. Verificar el contrato del host

Copia `checks/check_host_contract.py` al backend del empleado y
ejecútalo en su mismo entorno Python (donde `app.*` y `sgmcp_adapter`
sean ambos importables - por eso este paso va DESPUÉS de instalar el
paquete, no antes):

```
python checks/check_host_contract.py
```

Si **cualquier** comprobación falla: DETENTE. No continúes, no adaptes
`assets/sgmcp_adapter/` a mano para que la comprobación pase, y no toques
todavía `app/main.py` (eso es el paso siguiente, y es el paso caro de
deshacer - este es el punto para frenar antes de llegar ahí). Reporta el
detalle exacto de cada fallo - el ajuste correcto se hace una vez en este
skill, no por empleado.

## 3. Declarar la modificación de `app/main.py` como divergencia aprobada

`app/main.py` es un archivo invariante de la plantilla, y **no existe
(a la fecha de este procedimiento) un punto de extensión sancionado**
para montar routers o registrar tools MCP sin tocarlo. Esto NO es un
hook soportado - es un fork deliberado y documentado de la plantilla,
que el verificador seguirá marcando como diferente de la baseline
incluso con la divergencia declarada. Procede así, en orden:

1. Identifica el invariante afectado (`app/main.py`, ruta relativa a los
   assets de la plantilla: `backend/app/main.py`) y explica qué contrato
   se rompe: se necesita registrar rutas/tools adicionales y la
   plantilla actual solo admite montaje explícito en ese archivo.
2. Confirma que no hay alternativa compatible con la arquitectura (ya se
   verificó: no hay `custom_mounts.py`, autodescubrimiento de routers ni
   hook de plugins).
3. **Obtén aceptación humana explícita de que el resultado será un fork
   de la plantilla**, con un correo real de quien aprueba - el
   constructor no puede autoaprobarse esto. No continúes sin esa
   aprobación.
4. Agrega la entrada a `wai-app.json`, conservando el resto del
   manifiesto, ANTES de modificar el archivo:
   ```json
   {
     "agent_app": {
       "notes": {
         "divergences": [
           {
             "file_path": "backend/app/main.py",
             "reason": "Montar el adaptador generico sgmcp_adapter.",
             "rationale": "Se agregan los imports y una llamada a mount(app, require_project_access) antes de mount_mcp(app). La plantilla actual no ofrece un punto de extension para este montaje. Se acepta mantener esta diferencia respecto de la baseline y revisarla al actualizar el constructor.",
             "date": "AAAA-MM-DD",
             "approver_email": "correo-real-de-quien-aprueba@dominio"
           }
         ]
       }
     }
   }
   ```
   Los cinco campos son obligatorios y no deben quedar vacíos.
   `approver_email` no debe inventarse ni rellenarse con un correo del
   propio constructor.
5. Modifica `app/main.py` agregando, inmediatamente antes de la llamada
   que monta el MCP interno del empleado (`mount_mcp(app)` o
   equivalente), después de los demás `app.include_router(...)`:
   ```python
   from app.auth import require_project_access
   from sgmcp_adapter.integration import mount

   adapter = mount(app, require_project_access)
   ```
   No cambies nada más de `app/main.py`. No modifiques ningún archivo
   dentro de `assets/sgmcp_adapter/`.
6. Ejecuta las verificaciones del constructor e informa explícitamente
   que la comparación de invariantes para `app/main.py` fallará - es el
   resultado esperado de un fork documentado, no un error a corregir.
   **No ejecutes `--fix-invariants` sobre este empleado**: restauraría
   el `app/main.py` canónico y borraría la integración.

## 4. Verificar de nuevo (distinto del verificador de invariantes del paso 3.6 - aquí se confirma que la integración de sgmcp_adapter funciona, no que la plantilla siga intacta)

- Vuelve a correr `checks/check_host_contract.py` - debe seguir pasando.
- Confirma que el backend arranca sin errores de importación.
- `GET /api/supabase/configuration` (autenticado como administrador de
  este empleado) debe devolver `configured: false`, sin excepciones ni
  404/500.
- `GET /api/supabase/session` debe responder; en modo `rls_personal`
  (el valor por defecto antes de configurar nada) debe indicar
  `connected: false`.

## 4b. Opcional: pruebas de integración con RDWAI real

`checks/test_adapter_integration.py` cubre persistencia real en base de
datos y la exposición de `response_shape` solo a administradores - cosas
que `tests/test_adapter_smoke.py` no puede probar porque está diseñado
para correr sin RDWAI instalado. Cópialo a `src/backend/tests/` de este
empleado (usa el `conftest.py` que ya trae la plantilla) y corre `pytest`
ahí. No es obligatorio para completar este procedimiento, pero si ya vas
a tener el backend arriba, es la verificación más fuerte disponible.

## 5. Qué NO hacer en este procedimiento

No llenes `PUT /api/supabase/configuration` aquí. Definir tablas,
columnas, modo de autenticación (`rls_personal` vs `service_role`) y
las llaves del proyecto de Supabase corresponde al contexto de negocio
específico de este empleado - es un paso posterior, fuera del alcance de
este skill técnico. Este procedimiento (backend) por sí solo no incluye la pantalla visual
para cargar esa configuración ni el login personal - solo los endpoints
(`GET/PUT /api/supabase/configuration`, `POST /api/supabase/
session`). Eso no significa que sean opcionales: continúa siempre con
`procedures/integrate_frontend.md` inmediatamente después de este
procedimiento - es la parte estándar que permite configurar sin tocar
código, no un paso condicionado a si el encargo lo pide.

## 6. Reportar

Entrega el control de vuelta indicando: resultado de los checks del
paso 4, referencia de la divergencia registrada en el paso 3, y
confirmación de que ambos endpoints responden como se espera. Si algo no
coincidió con lo descrito aquí, repórtalo en vez de resolverlo por tu
cuenta.
