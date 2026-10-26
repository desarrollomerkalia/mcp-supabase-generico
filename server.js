import express from 'express';
import cors from 'cors';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { SSEServerTransport } from '@modelcontextprotocol/sdk/server/sse.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { createClient } from '@supabase/supabase-js';

const app = express();
app.use(cors());

// Logger
app.use((req, res, next) => {
  console.log(`[RDWAI] ${req.method} ${req.url}`);
  next();
});

const transports = new Map();

function createMcpServer() {
  const server = new Server({
    name: "Supabase-Generico-Completo-MCP",
    version: "2.0.0"
  }, {
    capabilities: { tools: {} }
  });

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const credenciales = {
      supabaseUrl: { type: "string", description: "URL del proyecto Supabase" },
      supabaseKey: { type: "string", description: "Service Role Key (recomendado para control total)" }
    };
    const tablaProp = { tabla: { type: "string", description: "Nombre de la tabla" } };

    return {
      tools: [
        {
          name: "supabase_leer",
          description: "Lee registros de una tabla con filtros opcionales.",
          inputSchema: {
            type: "object",
            properties: { 
              ...credenciales, ...tablaProp, 
              columnas: { type: "string", description: "Columnas a devolver (ej. 'id, nombre')", default: "*" },
              filtros: { type: "object", description: "Filtros clave-valor (ej. {\"estado\": \"activo\"})" },
              limite: { type: "number", default: 50 } 
            },
            required: ["supabaseUrl", "supabaseKey", "tabla"]
          }
        },
        {
          name: "supabase_insertar",
          description: "Inserta uno o múltiples registros en una tabla.",
          inputSchema: {
            type: "object",
            properties: { ...credenciales, ...tablaProp, datos: { type: "object", description: "JSON o Array con los datos" } },
            required: ["supabaseUrl", "supabaseKey", "tabla", "datos"]
          }
        },
        {
          name: "supabase_actualizar",
          description: "Actualiza registros filtrando por una columna.",
          inputSchema: {
            type: "object",
            properties: { 
              ...credenciales, ...tablaProp, 
              columna_filtro: { type: "string", description: "Columna para buscar (ej. 'id')" },
              valor_filtro: { type: "string", description: "Valor a buscar" },
              datos: { type: "object", description: "JSON con los datos a actualizar" }
            },
            required: ["supabaseUrl", "supabaseKey", "tabla", "columna_filtro", "valor_filtro", "datos"]
          }
        },
        {
          name: "supabase_eliminar",
          description: "Elimina registros filtrando por una columna.",
          inputSchema: {
            type: "object",
            properties: { 
              ...credenciales, ...tablaProp, 
              columna_filtro: { type: "string" },
              valor_filtro: { type: "string" }
            },
            required: ["supabaseUrl", "supabaseKey", "tabla", "columna_filtro", "valor_filtro"]
          }
        },
        {
          name: "supabase_upsertar",
          description: "Inserta un registro o lo actualiza si ya existe la clave primaria.",
          inputSchema: {
            type: "object",
            properties: { ...credenciales, ...tablaProp, datos: { type: "object" } },
            required: ["supabaseUrl", "supabaseKey", "tabla", "datos"]
          }
        },
        {
          name: "supabase_rpc",
          description: "Ejecuta una función de Postgres (Stored Procedure).",
          inputSchema: {
            type: "object",
            properties: { 
              ...credenciales, 
              funcion: { type: "string", description: "Nombre de la función RPC" },
              parametros: { type: "object", description: "JSON con los parámetros de la función" }
            },
            required: ["supabaseUrl", "supabaseKey", "funcion"]
          }
        },
        {
          name: "supabase_auth_listar_usuarios",
          description: "Lista los usuarios de Auth (Requiere Service Role Key).",
          inputSchema: {
            type: "object",
            properties: { ...credenciales },
            required: ["supabaseUrl", "supabaseKey"]
          }
        }
      ]
    };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    const { supabaseUrl, supabaseKey } = args;
    const supabase = createClient(supabaseUrl, supabaseKey);

    try {
      if (name === "supabase_leer") {
        let query = supabase.from(args.tabla).select(args.columnas || '*').limit(args.limite || 50);
        if (args.filtros) {
          for (const [key, value] of Object.entries(args.filtros)) {
            query = query.eq(key, value);
          }
        }
        const { data, error } = await query;
        if (error) throw error;
        return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
      } 
      
      if (name === "supabase_insertar") {
        const { data, error } = await supabase.from(args.tabla).insert(args.datos).select();
        if (error) throw error;
        return { content: [{ type: "text", text: `Insertado: ${JSON.stringify(data)}` }] };
      }

      if (name === "supabase_actualizar") {
        const { data, error } = await supabase.from(args.tabla).update(args.datos).eq(args.columna_filtro, args.valor_filtro).select();
        if (error) throw error;
        return { content: [{ type: "text", text: `Actualizado: ${JSON.stringify(data)}` }] };
      }

      if (name === "supabase_eliminar") {
        const { data, error } = await supabase.from(args.tabla).delete().eq(args.columna_filtro, args.valor_filtro).select();
        if (error) throw error;
        return { content: [{ type: "text", text: `Eliminado: ${JSON.stringify(data)}` }] };
      }

      if (name === "supabase_upsertar") {
        const { data, error } = await supabase.from(args.tabla).upsert(args.datos).select();
        if (error) throw error;
        return { content: [{ type: "text", text: `Upsert exitoso: ${JSON.stringify(data)}` }] };
      }

      if (name === "supabase_rpc") {
        const { data, error } = await supabase.rpc(args.funcion, args.parametros || {});
        if (error) throw error;
        return { content: [{ type: "text", text: `Resultado de función: ${JSON.stringify(data)}` }] };
      }

      if (name === "supabase_auth_listar_usuarios") {
        const { data, error } = await supabase.auth.admin.listUsers();
        if (error) throw error;
        return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
      }

      throw new Error("Herramienta no encontrada");
    } catch (error) {
      return { content: [{ type: "text", text: `Error de Supabase: ${error.message}` }], isError: true };
    }
  });

  return server;
}

// Respuestas a pings HEAD
app.head('/', (req, res) => res.status(200).end());
app.head('/sse', (req, res) => res.status(200).end());

// Manejador SSE
const handleSse = async (req, res) => {
  console.log(`[SSE GET] Nueva conexion desde ${req.url}`);
  const server = createMcpServer();
  const endpoint = req.path || '/';
  const transport = new SSEServerTransport(endpoint, res);
  
  transports.set(transport.sessionId, transport);

  const keepAlive = setInterval(() => {
    res.write(': keepalive\n\n');
  }, 15000);
  
  req.on('close', () => {
    console.log(`[SSE CLOSE] Sesion finalizada ${transport.sessionId}`);
    clearInterval(keepAlive);
    setTimeout(() => {
      transports.delete(transport.sessionId);
    }, 10000);
  });

  await server.connect(transport);
};

// Manejador POST
const handleMessage = async (req, res) => {
  const sessionId = req.query.sessionId;
  let transport = transports.get(sessionId) || Array.from(transports.values())[0];

  if (transport) {
    // Acomodar la URL requerida internamente por el SDK de MCP
    const endpoint = transport._endpoint || '/';
    req.url = `${endpoint}?sessionId=${transport.sessionId}`;

    const origWriteHead = res.writeHead;
    res.writeHead = function(statusCode, ...args) {
      if (!res.headersSent) {
        try {
          res.setHeader('Content-Type', 'application/json');
        } catch (e) {}
      }
      return origWriteHead.call(this, 200, ...args);
    };

    const origEnd = res.end;
    res.end = function(chunk, encoding, cb) {
      if (!res.headersSent) {
        try {
          res.setHeader('Content-Type', 'application/json');
        } catch (e) {}
      }
      if (chunk === 'Accepted' || chunk === 'Accepted\n' || !chunk) {
        chunk = JSON.stringify({ status: "ok", message: "Accepted" });
      }
      return origEnd.call(this, chunk, encoding, cb);
    };

    await transport.handlePostMessage(req, res);
  } else {
    res.status(200).json({ status: "ok", message: "Accepted" });
  }
};

app.get('/sse', handleSse);
app.get('/', handleSse);

app.post('/message', handleMessage);
app.post('/sse/message', handleMessage);
app.post('/sse', handleMessage);
app.post('/', handleMessage);

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servidor MCP genérico de Supabase corriendo en el puerto ${PORT}`);
});