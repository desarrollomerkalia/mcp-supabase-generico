# Presentación De La Vinculación Por Canal

Este contrato se aplica a empleados que YA implementan la vinculación de canal.
No incorpora ese flujo a los assets que todavía solo aceptan chat web, ni cambia
autenticación, identidad, caducidad, RLS o permisos de escritura.

## Origen Del Enlace

- Configurar `public_base_url` desde Operaciones → Conectores → Supabase.
- Persistirlo en el payload cifrado `sgmcp_adapter_connection` de `AppSetting`,
  utilizando los mismos permisos administrativos y primitivas de cifrado.
- Resolver override administrativo → `APP_PUBLIC_BASE_URL` → no disponible.
- Aceptar únicamente origen HTTPS, sin credenciales, ruta salvo `/`, query ni
  fragmento. No consultar el destino por HTTP para validarlo.
- Cargar la configuración persistida antes de construir el primer enlace.
- Construir `{origen}/supabase-canal-verificar?token={token_original}`. No
  reemplazar ni resumir el token. No emitir rutas relativas hacia WhatsApp.
- Si no hay origen válido, devolver `supabase_public_base_url_required` y
  explicar que un administrador debe configurar la URL pública, sin emitir token.
- GET/PUT `/configuration` expone `public_base_url`, `effective_public_base_url`
  y `public_base_url_source` (`configuration`, `environment` o `null`).
- Campo omitido o `null` conserva el override; cadena vacía lo elimina. Leer
  configuraciones antiguas sin el campo conserva el fallback al entorno.
- Un cambio exclusivo de esta URL se aplica después del commit y no reconfigura
  credenciales, sesiones ni la generación que protege operaciones en curso.
- PUT distingue `changed` (cualquier cambio) de `access_changed` (cambio de
  acceso). El frontend solo invalida datos y refresca sesión con el segundo;
  editar únicamente la URL actualiza los metadatos de configuración.

## Mensaje Canónico

El backend sustituye únicamente `{enlace_absoluto}` en este texto y devuelve el
resultado completo en `message` del error `supabase_channel_not_verified`:

```text
*Vincula tu cuenta para continuar*

Para atender tu consulta, necesitas vincular este WhatsApp con tu cuenta de Supabase.

*Abre este enlace:*
{enlace_absoluto}

Si aparece el acceso a WAI, inicia sesión. Después introduce tu contraseña de Supabase y pulsa «Verificar y vincular».

*El enlace vence en 30 minutos.*
No compartas tu contraseña por WhatsApp.

Cuando termines, vuelve aquí y repite tu consulta.
```

El enlace va solo en su línea, sin backticks, corchetes ni punto final añadido.
Conservar asteriscos y saltos de línea de WhatsApp. Comprobar el límite real de
longitud del transporte para no truncar el enlace ni el mensaje.

## Regla Del Skill Operativo

Incluir esta regla en el skill que recibe el agente EN EJECUCIÓN, no solamente
en este skill de construcción:

> Si el resultado trae `code: "supabase_channel_not_verified"`, asigna a
> `reply.body` el contenido íntegro de su campo `message`, exactamente como lo
> recibiste. No lo reescribas, resumas, traduzcas ni añadas introducciones,
> despedidas o explicaciones. Conserva los saltos de línea, los asteriscos de
> WhatsApp y la URL completa con su token.

No solicitar contraseñas por el canal. No añadir esta excepción específica al
ejecutor genérico de canales. Una instrucción al modelo no garantiza por sí
sola la copia literal: distinguir pruebas del backend/transporte de una prueba
real del agente, que debe comparar `reply.body` con `message` carácter a carácter.

## Comprobaciones

- Ausencia de panel, servicio y llamadas de diagnóstico temporal en producción.
- Retorno después del login WAI conserva token y POST de vinculación.
- Override, fallback, borrado, persistencia cifrada y recarga tras reinicio.
- Solo administradores del empleado pueden consultar o editar la configuración.
- Cambiar URL no invalida sesiones; cambios de acceso conservan su invalidación.
- Error MCP preserva `message` completo; transporte no altera formato ni URL.
- Ningún dominio particular hardcodeado, ni cambios en autenticación o RLS.
