import express from 'express';
import cors from 'cors';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { SSEServerTransport } from '@modelcontextprotocol/sdk/server/sse.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { createClient } from '@supabase/supabase-js';

const app = express();
app.use(cors());
app.use(express.json());

// Logger de diagnóstico
app.use((req, res, next) => {
  console.log(`[RDWAI] ${req.method} ${req.url}`);
  next();
});

// Helper para instanciar cliente de Supabase (Soporta RLS vía userToken)
function getSupabaseClient(args) {
  const { supabaseUrl, supabaseKey, userToken } = args;
  const options = {};

  if (userToken) {
    options.global = {
      headers: {
        Authorization: `Bearer ${userToken}`
      }
    };
  }

  return createClient(supabaseUrl, supabaseKey, options);
}

const credencialesBase = {
  supabaseUrl: { type: "string", description: "URL del proyecto Supabase" },
  supabaseKey: { type: "string", description: "Clave de Supabase (anon_key para RLS o service_role para admin)" },
  userToken: { type: "string", description: "JWT de acceso del usuario autenticado (Opcional, activa RLS)" }
};

const TOOLS_LIST = [
  {
    name: "supabase_leer",
    description: "Lee registros de una tabla respetando RLS si se provee userToken.",
    inputSchema: {
      type: "object",
      properties: { 
        ...credencialesBase,
        tabla: { type: "string", description: "Nombre de la tabla" },
        columnas: { type: "string", description: "Columnas a devolver (ej. 'id, nombre')", default: "*" },
        filtros: { type: "object", description: "Filtros clave-valor (ej. {\"estado\": \"activo\"})" },
        limite: { type: "number", default: 50 } 
      },
      required: ["supabaseUrl", "supabaseKey", "tabla"]
    }
  },
  {
    name: "supabase_insertar",
    description: "Inserta uno o múltiples registros respetando RLS si se provee userToken.",
    inputSchema: {
      type: "object",
      properties: { 
        ...credencialesBase,
        tabla: { type: "string", description: "Nombre de la tabla" },
        datos: { type: "object", description: "JSON o Array con los datos" } 
      },
      required: ["supabaseUrl", "supabaseKey", "tabla", "datos"]
    }
  },
  {
    name: "supabase_actualizar",
    description: "Actualiza registros filtrando por columna respetando RLS si se provee userToken.",
    inputSchema: {
      type: "object",
      properties: { 
        ...credencialesBase,
        tabla: { type: "string", description: "Nombre de la tabla" },
        columna_filtro: { type: "string", description: "Columna para buscar (ej. 'id')" },
        valor_filtro: { type: "string", description: "Valor a buscar" },
        datos: { type: "object", description: "JSON con los datos a actualizar" }
      },
      required: ["supabaseUrl", "supabaseKey", "tabla", "columna_filtro", "valor_filtro", "datos"]
    }
  },
  {
    name: "supabase_eliminar",
    description: "Elimina registros respetando RLS si se provee userToken.",
    inputSchema: {
      type: "object",
      properties: { 
        ...credencialesBase,
        tabla: { type: "string" },
        columna_filtro: { type: "string" },
        valor_filtro: { type: "string" }
      },
      required: ["supabaseUrl", "supabaseKey", "tabla", "columna_filtro", "valor_filtro"]
    }
  },
  {
    name: "supabase_upsertar",
    description: "Inserta o actualiza registros respetando RLS si se provee userToken.",
    inputSchema: {
      type: "object",
      properties: { 
        ...credencialesBase,
        tabla: { type: "string" },
        datos: { type: "object" } 
      },
      required: ["supabaseUrl", "supabaseKey", "tabla", "datos"]
    }
  },
  {
    name: "supabase_rpc",
    description: "Ejecuta una función de Postgres (Stored Procedure) bajo el contexto del usuario si se provee userToken.",
    inputSchema: {
      type: "object",
      properties: { 
        ...credencialesBase,
        funcion: { type: "string", description: "Nombre de la función RPC" },
        parametros: { type: "object", description: "JSON con los parámetros" }
      },
      required: ["supabaseUrl", "supabaseKey", "funcion"]
    }
  },
  {
    name: "supabase_auth_listar_usuarios",
    description: "Lista los usuarios de Auth (Requiere Service Role Key obligatoriamente).",
    inputSchema: {
      type: "object",
      properties: { 
        supabaseUrl: { type: "string", description: "URL del proyecto Supabase" },
        supabaseKey: { type: "string", description: "Service Role Key" }
      },
      required: ["supabaseUrl", "supabaseKey"]
    }
  },
  {
    name: "supabase_ejecutar_sql",
    description: "Ejecuta sentencias SQL arbitrarias (Solo administradores / Service Role Key).",
    inputSchema: {
      type: "object",
      properties: { 
        supabaseUrl: { type: "string", description: "URL del proyecto Supabase" },
        supabaseKey: { type: "string", description: "Service Role Key" },
        sql: { type: "string", description: "Sentencia SQL completa a ejecutar" }
      },
      required: ["supabaseUrl", "supabaseKey", "sql"]
    }
  }
];

async function handleToolCall(name, args) {
  const supabase = getSupabaseClient(args);

  if (name === "supabase_leer") {
    let query = supabase.from(args.tabla).select(args.columnas || '*').limit(args.limite || 50);
    if (args.filtros) {
      for (const [key, value] of Object.entries(args.filtros)) {
        query = query.eq(key, value);
      }
    }
    const { data, error } = await query;
    if (error) throw error;
    return [{ type: "text", text: JSON.stringify(data, null, 2) }];
  } 
  
  if (name === "supabase_insertar") {
    const { data, error } = await supabase.from(args.tabla).insert(args.datos).select();
    if (error) throw error;
    return [{ type: "text", text: `Insertado: ${JSON.stringify(data)}` }];
  }

  if (name === "supabase_actualizar") {
    const { data, error } = await supabase.from(args.tabla).update(args.datos).eq(args.columna_filtro, args.valor_filtro).select();
    if (error) throw error;
    return [{ type: "text", text: `Actualizado: ${JSON.stringify(data)}` }];
  }

  if (name === "supabase_eliminar") {
    const { data, error } = await supabase.from(args.tabla).delete().eq(args.columna_filtro, args.valor_filtro).select();
    if (error) throw error;
    return [{ type: "text", text: `Eliminado: ${JSON.stringify(data)}` }];
  }

  if (name === "supabase_upsertar") {
    const { data, error } = await supabase.from(args.tabla).upsert(args.datos).select();
    if (error) throw error;
    return [{ type: "text", text: `Upsert exitoso: ${JSON.stringify(data)}` }];
  }

  if (name === "supabase_rpc") {
    const { data, error } = await supabase.rpc(args.funcion, args.parametros || {});
    if (error) throw error;
    return [{ type: "text", text: `Resultado de función: ${JSON.stringify(data)}` }];
  }

  if (name === "supabase_auth_listar_usuarios") {
    const { data, error } = await supabase.auth.admin.listUsers();
    if (error) throw error;
    return [{ type: "text", text: JSON.stringify(data, null, 2) }];
  }

  if (name === "supabase_ejecutar_sql") {
    const { data, error } = await supabase.rpc('exec_sql', { sql_query: args.sql });
    if (error) throw new Error(`Error ejecutando SQL: ${error.message}`);
    return [{ type: "text", text: `Resultado SQL: ${JSON.stringify(data)}` }];
  }

  throw new Error("Herramienta no encontrada");
}

const transports = new Map();

function createMcpServer() {
  const server = new Server({
    name: "Supabase-Generico-Completo-MCP",
    version: "2.2.0"
  }, {
    capabilities: { tools: {} }
  });

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS_LIST }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    try {
      const content = await handleToolCall(request.params.name, request.params.arguments);
      return { content };
    } catch (error) {
      return { content: [{ type: "text", text: `Error: ${error.message}` }], isError: true };
    }
  });

  return server;
}

// Respuestas HEAD y DELETE
app.head('/', (req, res) => res.status(200).end());
app.head('/sse', (req, res) => res.status(200).end());

app.delete('/', (req, res) => res.status(200).json({ status: "ok" }));
app.delete('/sse', (req, res) => res.status(200).json({ status: "ok" }));
app.delete('/message', (req, res) => res.status(200).json({ status: "ok" }));

const handleSse = async (req, res) => {
  console.log(`[SSE GET] Nueva conexion desde ${req.url}`);
  const server = createMcpServer();
  const transport = new SSEServerTransport('/message', res);
  transports.set(transport.sessionId, transport);

  const keepAlive = setInterval(() => {
    try { res.write(': keepalive\n\n'); } catch (e) {}
  }, 15000);
  
  req.on('close', () => {
    console.log(`[SSE CLOSE] Sesion cerrada ${transport.sessionId}`);
    clearInterval(keepAlive);
    setTimeout(() => transports.delete(transport.sessionId), 60000);
  });

  await server.connect(transport);
};

app.get('/sse', handleSse);
app.get('/', handleSse);

// Manejador POST directo
const handleMessage = async (req, res) => {
  const body = req.body;

  if (body && typeof body === 'object' && body.jsonrpc === '2.0') {
    const id = body.id !== undefined ? body.id : null;
    const method = body.method;

    console.log(`[JSON-RPC Directo] Procesando método: ${method}`);

    if (method === 'initialize') {
      return res.status(200).json({
        jsonrpc: "2.0",
        id: id,
        result: {
          protocolVersion: "2024-11-05",
          capabilities: { tools: {} },
          serverInfo: { name: "Supabase-Generico-Completo-MCP", version: "2.2.0" }
        }
      });
    }

    if (method === 'notifications/initialized') {
      return res.status(200).json({ jsonrpc: "2.0", result: {} });
    }

    if (method === 'tools/list') {
      return res.status(200).json({
        jsonrpc: "2.0",
        id: id,
        result: { tools: TOOLS_LIST }
      });
    }

    if (method === 'tools/call') {
      try {
        const { name, arguments: args } = body.params || {};
        const content = await handleToolCall(name, args);
        return res.status(200).json({
          jsonrpc: "2.0",
          id: id,
          result: { content }
        });
      } catch (error) {
        return res.status(200).json({
          jsonrpc: "2.0",
          id: id,
          error: { code: -32603, message: error.message }
        });
      }
    }
  }

  const sessionId = req.query.sessionId;
  let transport = sessionId ? transports.get(sessionId) : Array.from(transports.values())[0];

  if (transport) {
    req.url = `/message?sessionId=${transport.sessionId}`;
    await transport.handlePostMessage(req, res);
  } else {
    res.status(200).json({ jsonrpc: "2.0", result: {}, id: 1 });
  }
};

app.post('/message', handleMessage);
app.post('/sse/message', handleMessage);
app.post('/sse', handleMessage);
app.post('/', handleMessage);

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servidor MCP genérico de Supabase corriendo en el puerto ${PORT}`);
});