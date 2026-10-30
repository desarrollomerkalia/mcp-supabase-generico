# Cómo replicar el skill `supabase-employee-integration` en otro tenant de RDWAI

Este documento es para ti (o para quien administre otro tenant), no para los
constructores de empleados. Asume que ya tienes acceso de administrador a ese
tenant y que el conector `sgmcp` (MCP genérico, catálogo id 51,
`https://mcp-supabase-generico.onrender.com`) ya existe ahí de forma idéntica
— por eso este documento NO incluye pasos para crear o configurar el
conector, solo el skill.

Repite este procedimiento una vez por cada tenant nuevo. No hay dependencia
entre tenants: el skill publicado en el tenant A no es visible ni reutilizable
desde el tenant B.

## 0. Qué vas a subir

Todo lo que necesitas vive en una sola carpeta autocontenida de este repo:

```
skill/supabase-employee-integration/
├── SKILL.md
├── VERSION                   (una sola línea: el número a publicar, ej. 1.0.7)
├── assets/
│   ├── sgmcp_adapter/        (paquete Python completo)
│   ├── pyproject.toml
│   └── frontend/             (componentes React/TS de la integración estándar)
├── procedures/
│   ├── integrate.md
│   └── integrate_frontend.md
├── checks/
│   ├── check_host_contract.py
│   └── test_adapter_integration.py
└── tests/
    └── test_adapter_smoke.py
```

No subas nada de `adapter/` (es el código fuente canónico de este repo, no lo
que se publica) ni `FEATURE_REQUEST_EXTENSION_POINT.md` (vive a propósito
fuera de la carpeta del skill — es una solicitud a usewai.com, no contenido
del skill).

Comprime exactamente la carpeta `supabase-employee-integration/` completa
(con ese nombre) en un `.zip`. Si vas a publicar una actualización sobre una
versión ya existente en el tenant de origen, antes de comprimir corre
`skill/sync_assets.sh` desde este repo para asegurarte de que
`assets/sgmcp_adapter`, `assets/pyproject.toml`, `tests/`, `assets/frontend` y
`checks/test_adapter_integration.py` reflejan el estado actual de `adapter/`.

El archivo `VERSION` es la única fuente de verdad del número a publicar -
antes de empaquetar, confirma que diga el número que de verdad quieres
publicar en todas partes (edítalo a mano en este repo si no es el caso).
No lo generes a mano en el prompt de cada tenant ni lo repitas de memoria:
el agente que publica debe leerlo del archivo.

## 1. Abre una sesión del constructor de WAI en el tenant destino

Entra al tenant nuevo con una cuenta con permisos para crear skills del
catálogo. Abre una sesión de "WAI Agent" (el chat constructor), no la de
construcción de un empleado — los skills se crean/publican desde ahí, no
desde la sesión de un empleado concreto.

## 2. Sube el `.zip` a esa sesión

Adjunta el archivo comprimido del paso 0 al chat.

## 3. Prompt para que el agente cree y publique el skill

Pega esto tal cual (ajusta solo el número de versión si no es la primera vez
que este tenant ve este skill):

```
Voy a darte un skill técnico ya terminado y verificado en producción en
otro tenant, para que lo publiques aquí tal cual, sin modificarlo.

Te adjunté un .zip con la carpeta completa "supabase-employee-integration/".
Haz esto, en orden:

1. Descomprime el .zip y lee SKILL.md completo para confirmar que entiendes
   su contrato (qué recibe, qué entrega, qué NO hace).
2. Lee el archivo VERSION en la raíz de la carpeta - es un solo número
   (ej. 1.0.7). Ese es el número que vas a publicar en el paso 4, tal
   cual, sin modificarlo ni proponer otro.
3. Crea el skill en el catálogo con estos datos exactos:
   - name: supabase-employee-integration
   - kind: functional (NO le asignes owner_agent_id — debe quedar disponible
     para cualquier empleado de este tenant, no atado a un agente específico)
   - description: usa literalmente el campo "description" del frontmatter de
     SKILL.md, sin resumirlo ni reescribirlo.
4. Sube como contenido de la versión del skill la carpeta completa tal como
   te la entregué, con la misma estructura de archivos y carpetas
   (SKILL.md, VERSION, assets/, procedures/, checks/, tests/). No
   reorganices archivos, no renombres nada, no "mejores" el contenido.
5. Publica esa versión usando EXACTAMENTE el número que leíste en VERSION
   en el paso 2 (no reinicies en 1.0.0 solo porque este tenant nunca vio
   el skill antes). Así "versión X" significa siempre el mismo contenido,
   sin importar el tenant - si cada tenant numerara desde cero, "1.0.0"
   significaría una cosa distinta en cada uno y tendrías que llevar una
   tabla de equivalencias para saber qué corresponde con qué.
6. Confirma, mostrándome evidencia concreta (no solo "listo"):
   - el nombre exacto, kind y versión con los que quedó publicado
   - que aparece en el selector de "Skills adicionales" al iniciar una
     sesión nueva de construcción de empleado en este tenant
   - que el conector sgmcp (catálogo id 51) ya existe en este tenant de
     forma independiente a este skill (solo confírmalo, no lo crees ni lo
     modifiques)

No interpretes, resumas ni "arregles" nada del contenido del skill. Si algo
en el proceso de publicación te obliga a cambiar un archivo para que pase
una validación de este tenant, DETENTE y repórtamelo con el detalle exacto
en vez de ajustarlo tú — cualquier ajuste al contenido se decide
centralmente, no por tenant.
```

## 4. Verifica tú mismo, no solo confíes en la respuesta del agente

Pide evidencia textual/literal (captura o copia del listado), no la
paráfrasis del modelo:
- El skill aparece listado con el nombre, `kind: functional` y la versión
  correctos.
- Al abrir una sesión nueva de construcción de empleado en ese tenant, el
  skill aparece como opción seleccionable en "Skills adicionales".

Si algo de esto falla, no lo arregles manualmente dentro de ese tenant —
vuelve aquí, corrige la causa en este repo (`adapter/` o la carpeta del
skill), corre `sync_assets.sh`, re-empaqueta y repite el proceso. La regla
dura del skill aplica también a su propia publicación: los ajustes se hacen
una vez, centralmente, no tenant por tenant.

## 5. Publicar una actualización futura (versión 1.0.x+1) en un tenant que ya tiene el skill

1. En este repo: haz el cambio en `adapter/`, corre la suite de pruebas,
   corre `skill/sync_assets.sh`, actualiza `SKILL.md` si el cambio lo
   amerita, y **edita `VERSION` al nuevo número antes de empaquetar** -
   si se te olvida este paso, el tenant destino publicaría el contenido
   nuevo con el número viejo, y "versión X" dejaría de significar lo mismo
   en todos lados.
2. Vuelve a comprimir `supabase-employee-integration/` completa (con el
   VERSION ya actualizado adentro).
3. En la sesión del constructor del tenant destino, sube el `.zip` nuevo y
   pide explícitamente una **nueva versión** del skill existente (no un
   skill nuevo con otro nombre) con un prompt análogo al del paso 3, pero
   indicando que es una actualización de una versión ya publicada, que lea
   el número desde VERSION igual que la primera vez, y que debe quedar
   como la versión vigente que verán las sesiones nuevas.
4. Repite la verificación del paso 4.

## Notas

- No existe (por ahora) un mecanismo para publicar en todos los tenants a la
  vez — es un proceso manual, tenant por tenant, hasta que la plataforma
  ofrezca algo distinto.
- Si en algún tenant el conector `sgmcp` no existe todavía o no es idéntico
  (otra URL, otro id de catálogo), detente antes del paso 1: este documento
  asume que ya es idéntico, y las instrucciones del skill (`procedures/`)
  dan por hecho ese mismo conector.
