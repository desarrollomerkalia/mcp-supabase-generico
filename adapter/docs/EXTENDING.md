# Extender el adaptador: flujos con efecto secundario en dos tablas

El paquete base cubre lectura/inserción/actualización/eliminación
genéricas sobre una tabla a la vez, con journal de idempotencia. Eso
cubre la mayoría de empleados. Lo que no cubre es un flujo como el
original de Mara: crear un registro en una tabla y, como parte de la
misma operación de negocio, cambiar un campo de otra tabla relacionada
(en ese caso: insertar en `rents` y marcar `rooms.status = busy`).

Esto es lógica de negocio, no configuración - no se puede expresar solo
con el allowlist de `ConnectionConfig`. Si un empleado nuevo realmente
necesita este patrón, créalo como una extensión explícita en vez de
forzarlo dentro de las cuatro herramientas genéricas.

## El patrón a seguir (tomado del `rental_flow.py` original)

1. Declara el flujo con sus propios nombres de tabla/columnas - no lo
   intentes meter en `insertar_registros`.
2. Reutiliza `Adapter.preflight_write` / `Adapter.validate_write_context`
   para la parte de sesión y alcance (eso sí es genérico, no lo
   reescribas).
3. El efecto secundario (el "luego actualiza la otra tabla") necesita su
   propio journal de fases, porque el punto medio entre las dos
   escrituras es un estado real que puede quedar a medias si el proceso
   se cae entre una y otra. El original resolvía esto guardando un
   registro de fase (`not_sent` / `partial` / `complete` / `unknown`) en
   `ToolCallLog`, no reintentando nunca una escritura externa que no
   pudiera probar que no se envió, y exigiendo que una operación
   "parcial" solo pueda continuarse (nunca reinsertarse) usando un
   identificador de operación (`op_id`) ya existente.
4. El catálogo de herramientas para este flujo se escribe a mano para
   ese negocio (como hacía `mcp_routes.tool_catalogue()` original con
   `crear_renta`/`editar_renta`) - no sale de `tool_catalogue.py`
   genérico.

## Por qué no se generalizó ya

Generalizar "tabla A, con estos campos requeridos, dispara una
actualización condicional en tabla B, con esta columna a este valor" es
posible, pero el punto donde vale la pena construirlo es cuando haya un
segundo o tercer empleado que lo necesite con una forma distinta a la de
Mara - antes de eso, cualquier abstracción sería una adivinanza sobre
casos que todavía no existen. Si llegas a ese punto, el primitivo a
extraer es exactamente los pasos 2 y 3 de arriba (preflight + journal de
fases), parametrizados por las dos tablas y la condición de enlace, no
una reescritura del motor completo.
