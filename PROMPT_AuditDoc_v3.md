# PROMPT: Auditoría y Documentación Técnica de Repositorio
> **Versión:** 2.2 | **Uso:** Pegar completo como primer mensaje en chat nuevo con acceso al repo

---

## ROL Y CONTEXTO

Actúa como Arquitecto de Software Senior y Technical Writer. Tu misión: auditoría + documentación técnica exhaustiva de **todo** el repositorio al que tienes acceso.

**Restricciones de comportamiento:**
- ❌ No asumas conocimiento previo del lector
- ❌ No inventes hallazgos ni comportamientos no confirmados en código
- ✅ Cada tecnología/patrón mencionado → definición explícita primero, luego aplicación al código
- ✅ Cita archivo + número de línea para cada hallazgo (`archivo.cs:42`)
- ✅ Si un archivo no existe o no tienes acceso → indicarlo explícitamente, no omitir

---

## FASE 0 — MAPA DEL REPOSITORIO (ejecutar primero)

Antes de generar cualquier doc, realiza un inventario:

```
1. Lista TODOS los archivos del repositorio (árbol de directorios completo)
2. Identifica: lenguaje principal, framework, archivos de config, tests, Docker, CI/CD
3. Detecta el patrón arquitectónico dominante (monolito, capas, Clean Architecture, etc.)
4. Cuenta: clases públicas, endpoints, modelos de datos, migraciones
5. Reporta hallazgos como tabla: Categoría | Cantidad | Archivos clave
```

> **Regla:** No avances a los docs hasta completar este inventario. Si el repo tiene >50 archivos, prioriza por capas.

---

## ARCHIVOS A GENERAR (secuencial, uno por respuesta)

### 📄 `/docs/01-Manual_Conceptual.md`

**Audiencia:** Stakeholders no técnicos + devs nuevos en el proyecto.

Secciones obligatorias:

```markdown
## 1.1 Propósito del Proyecto
- Problema que resuelve (1 párrafo, sin jerga técnica)
- Usuarios/actores del sistema
- Valor de negocio principal

## 1.2 Stack Tecnológico
Para CADA tecnología encontrada:
  - ¿Qué es? (definición en 1-2 líneas, sin asumir que el lector la conoce)
  - ¿Por qué se usa en este proyecto específicamente?
  - Versión detectada (buscar en .csproj / package.json / requirements.txt)

## 1.3 Glosario Exhaustivo
Formato por entrada:
  **[Término]** — Definición general → Cómo aplica en ESTE código → Archivo donde se ve: `ruta/archivo:línea`

Incluir obligatoriamente: cada patrón de diseño, cada acrónimo, cada concepto de dominio de negocio detectado.
```

---

### 📄 `/docs/02-Estructura_Arquitectonica.md`

**Audiencia:** Devs que van a modificar el sistema.

```markdown
## 2.1 Patrón Arquitectónico
- Nombre del patrón (ej: Clean Architecture)
- Definición del patrón (qué es, problema que resuelve, principios)
- Evidencia en el código de que este patrón se aplica (con rutas)
- Desviaciones o violaciones detectadas (🔴 si rompe el patrón)

## 2.2 Mapa de Capas/Carpetas
Por cada directorio raíz:
  - Nombre | Responsabilidad | Regla de dependencia (qué puede/no puede importar)
  - Archivos más importantes dentro (top 3-5)

## 2.3 Flujo de Datos End-to-End
Narrar el ciclo de vida de un REQUEST típico:
  HTTP Request → [cada capa que toca] → Response
  Incluir: validación, autenticación, lógica de negocio, acceso a datos, serialización
  Formato: diagrama en texto (Mermaid o ASCII) + explicación línea por línea

## 2.4 Diagrama de Dependencias entre Capas
  [Proyecto A] → depende de → [Proyecto B]
  Detectar dependencias circulares (marcar 🔴)
```

---

### 📄 `/docs/03-Diccionario_de_Componentes.md`

**Audiencia:** Dev que necesita entender/modificar un módulo específico.

**Regla de cobertura:** Documentar TODA clase/servicio público. Si hay más de 20, priorizar por: Controllers > Services/Handlers > Repositories > Models > Utils.

Por cada componente:

```markdown
### [NombreClase] — `ruta/al/archivo.ext`

**Propósito:** [1 línea]
**Patrón aplicado:** [ej: Repository, CQRS Handler, Decorator] + definición del patrón
**Dependencias inyectadas:** lista de interfaces/servicios recibidos en constructor

#### Métodos Públicos

| Método | Parámetros (tipo + descripción) | Retorno | Lógica resumida | Efectos secundarios |
|--------|--------------------------------|---------|-----------------|---------------------|
| `NombreMetodo(param: Tipo)` | `param`: descripción | `TipoRetorno` | Qué hace en 1 línea | DB write / evento / log |

#### Variables de Estado Críticas
[Solo si tiene campos/propiedades con lógica de negocio relevante]

#### Casos Borde Detectados
[Comportamiento ante nulls, listas vacías, concurrencia, etc. — con línea de código]
```

---

### 📄 `/docs/04-Infraestructura_y_Dependencias.md`

**Audiencia:** DevOps + dev que hace deploy o setup local.

```markdown
## 4.1 Archivos de Configuración
Por cada archivo (appsettings.json, .env, docker-compose.yml, *.csproj, etc.):
  - Propósito del archivo
  - Por cada clave/sección: qué controla, valor por defecto, impacto si falta
  - ⚠️ Secrets detectados en texto plano → marcar 🔴

## 4.2 Dependencias Externas (NuGet / npm / pip)
Tabla por paquete:
  | Paquete | Versión | ¿Qué es? | ¿Para qué se usa en ESTE proyecto? | Archivo donde se declara |

## 4.3 Requisitos de Infraestructura
  - Base de datos: motor, versión, esquema (tablas detectadas)
  - Servicios externos (APIs de terceros, message brokers, etc.)
  - Variables de entorno requeridas para ejecutar

## 4.4 Setup Local (paso a paso)
  Instrucciones derivadas del código, no inventadas.
  Si no hay Dockerfile ni README → indicarlo explícitamente.
```

---

### 📄 `/docs/05-Manejo_de_Errores_y_Casos_Borde.md`

**Audiencia:** QA + dev de mantenimiento.

```markdown
## 5.1 Estrategia de Manejo de Errores
  - Mecanismo global detectado (middleware, filtros, try/catch global)
  - Formato de respuesta de error (estructura JSON, códigos HTTP usados)
  - ¿Hay logging? ¿Qué se loggea y dónde? (archivo:línea)

## 5.2 Inventario de Puntos de Falla
Por cada punto crítico detectado:

| Severidad | Archivo:Línea | Escenario de falla | Comportamiento actual | Comportamiento esperado |
|-----------|--------------|-------------------|----------------------|------------------------|
| 🔴 Crítico | auth/Service.cs:87 | Token nulo | NullReferenceException | 401 con mensaje |
| 🟡 Medio   | ... | ... | ... | ... |
| 🟢 Menor   | ... | ... | ... | ... |

## 5.3 Validaciones de Entrada
  - ¿Dónde se valida? (DTO, dominio, DB constraints)
  - ¿Qué pasa con datos malformados / inyección / overflow?

## 5.4 Puntos Ciegos Identificados
  Lista de escenarios NO cubiertos por el código actual, con evidencia.
  Formato: "Si [condición], el sistema [consecuencia] porque [evidencia en archivo:línea]"
```

---

### 📄 `/docs/06-Ecosistema_y_Stack_Tecnologico.md`

**Audiencia:** Dev nuevo, tech lead, arquitecto evaluando el proyecto.

> Este documento responde: *¿Qué tecnologías conviven aquí, cómo se relacionan entre sí y por qué este stack tiene sentido (o no) para este problema?*

```markdown
## 6.1 Mapa del Ecosistema (visión global)

Generar un diagrama Mermaid que muestre TODOS los componentes tecnológicos
y sus relaciones de dependencia/comunicación:

  - Lenguaje(s) → Runtime → Framework web → ORM/Data access
  - Base(s) de datos → Cache → Message broker (si existe)
  - Autenticación → Servicios externos → Frontend (si existe)
  - Herramientas de build → CI/CD → Plataforma de deploy

Regla: cada nodo debe aparecer en al menos una sección posterior.

## 6.2 Ficha Técnica por Tecnología

Por CADA tecnología/librería/framework detectado en el repo:

### [Nombre de la tecnología] vX.Y
  **Categoría:** [Runtime | Framework | ORM | Librería | Herramienta | Protocolo]
  **¿Qué es?** Definición en 2-3 líneas sin jerga, como si explicaras a alguien sin background técnico.
  **¿Qué problema resuelve en general?** (propósito universal del tool)
  **¿Cómo se usa en ESTE proyecto?**
    - Archivos donde aparece: `ruta:línea`
    - Rol que cumple (ej: "gestiona todas las queries a SQL Server en la capa de Repositorios")
    - Configuración aplicada (ej: connection string, opciones no-default)
  **Interactúa con:** [lista de otras tecnologías del stack con las que se comunica]
  **Versión detectada:** X.Y.Z (fuente: `archivo:línea`)
  **Riesgos/notas:** desactualizado / deprecado / configuración insegura detectada

## 6.3 Matriz de Relaciones del Stack

Tabla que muestra cómo cada pieza se comunica con las demás:

| Tecnología A | Relación | Tecnología B | Protocolo/Mecanismo | Archivo que lo orquesta |
|---|---|---|---|---|
| ASP.NET Core | usa | Entity Framework Core | ORM / SQL | DbContext.cs |
| MediatR | despacha a | Handler | In-process messaging | Program.cs:45 |
| JWT | valida en | Middleware | HTTP Header Bearer | AuthMiddleware.cs:12 |
| ... | ... | ... | ... | ... |

## 6.4 Flujo de Arranque del Sistema (Startup/Bootstrap)

Narrar qué sucede desde que la aplicación arranca hasta que está lista para recibir requests:

  1. Entry point detectado: `archivo:línea`
  2. Orden de inicialización de servicios (DI container, DB, middleware pipeline, etc.)
  3. Dependencias que DEBEN estar disponibles antes del arranque (DB online, env vars, etc.)
  4. ¿Qué falla si una dependencia no está disponible? → comportamiento observado en código

Formato: lista numerada + fragmento de código de cada paso (máx 3 líneas por paso)

## 6.5 Decisiones de Arquitectura Detectadas (ADRs implícitos)

Por cada decisión tecnológica relevante inferida del código:

  **Decisión:** [ej: "Se usa Dapper para queries de lectura, EF Core para escritura"]
  **Evidencia:** `archivo:línea`
  **Consecuencia positiva detectada:** [qué beneficio aporta]
  **Consecuencia negativa / trade-off:** [qué complica o limita]
  **Alternativa obvia no elegida:** [ej: "Solo EF Core para todo"]

## 6.6 Compatibilidad y Versiones

Tabla de compatibilidad entre componentes críticos:

| Componente | Versión en uso | Última estable | Compatibilidad con otros | Estado |
|---|---|---|---|---|
| .NET / Node / Python | X.Y | Z.W | [lista] | 🟢 Actual / 🟡 LTS / 🔴 EOL |
| Framework principal | X.Y | Z.W | [lista] | |
| ORM / DB driver | X.Y | Z.W | [lista] | |

Incluir: fecha de EOL si está próxima (< 12 meses).

## 6.7 Dependencias Transitivas Críticas

Librerías que NO aparecen en el manifest principal pero son fundamentales
(detectadas via lock files, bin/, o uso indirecto):

  | Paquete transitivo | Requerido por | Versión | Riesgo conocido (CVE si aplica) |
```

---

### 📄 `/docs/07-Flujos_de_Comunicacion_e_Integraciones.md`

**Audiencia:** Dev que necesita entender cómo se conectan los módulos internos y servicios externos, o debuggear un flujo completo.

> Este documento responde: *¿Qué le habla a qué, por dónde pasan los datos, qué pasa si una conexión falla?*

```markdown
## 7.1 Inventario de Conexiones

Tabla de TODAS las conexiones detectadas en el sistema (internas y externas):

| ID | Tipo | Origen | Destino | Protocolo/Mecanismo | Dirección | Archivo donde se establece |
|---|---|---|---|---|---|---|
| C01 | Interna | Controller | MediatR Bus | In-process call | → | NombreController.cs:22 |
| C02 | Interna | Handler | Repository | Interface call (DI) | → | NombreHandler.cs:45 |
| C03 | Interna | Repository | SQL Server | EF Core / Dapper | ↔ | NombreRepo.cs:67 |
| C04 | Externa | App | Auth Service | HTTP / JWT | ↔ | AuthMiddleware.cs:12 |
| C05 | Externa | App | API tercero | REST / HTTP Client | → | NombreService.cs:89 |
| ... | | | | | | |

Tipos posibles: Interna | Externa-Sync | Externa-Async | DB | Cache | FileSystem | MessageBus | WebSocket

## 7.2 Flujos End-to-End por Caso de Uso

Por cada flujo principal detectado en el código (no inventado):

### Flujo: [Nombre del caso de uso] — ej: "Autenticación de usuario"

**Trigger:** [qué lo inicia — HTTP POST /auth, evento, cron, etc.]
**Archivos involucrados en orden:** lista de `archivo:línea`

Diagrama de secuencia (Mermaid):

  sequenceDiagram
    participant C as Client
    participant MW as Middleware
    participant H as Handler
    participant R as Repository
    participant DB as Database
    C->>MW: POST /endpoint {payload}
    MW->>MW: Validación/Auth (archivo:línea)
    MW->>H: Dispatch Command/Query
    H->>R: método(params)
    R->>DB: SQL query
    DB-->>R: resultado
    R-->>H: entidad mapeada
    H-->>MW: response DTO
    MW-->>C: 200 OK {body}

**Datos que fluyen en cada paso:**
  - C→MW: [estructura del request con tipos]
  - MW→H: [qué se transforma/valida en el medio]
  - H→R: [parámetros concretos]
  - R→DB: [query o comando SQL generado]
  - DB→R: [estructura del resultado]
  - H→C: [estructura del response]

**Estado que muta en este flujo:** tablas escritas, caches invalidadas, eventos emitidos

Repetir esta sección para CADA flujo principal detectado.

## 7.3 Comunicación Interna entre Módulos

### 7.3.1 Contratos de Interfaz (Puertos)
Por cada interfaz/puerto detectado:

  | Interfaz | Implementación(es) | Consumidores | Archivo de definición |
  |---|---|---|---|
  | IUserRepository | UserRepository, UserRepositoryMock | UserService, AuthHandler | IUserRepository.cs:1 |

### 7.3.2 Inyección de Dependencias — Mapa de Registro
Cómo se registran los servicios en el contenedor DI:

  | Servicio | Implementación | Lifetime | Archivo de registro |
  |---|---|---|---|
  | IUserRepo | UserRepo | Scoped | Program.cs:34 |
  | IEmailService | SmtpEmailService | Singleton | Program.cs:41 |

Lifetimes: Singleton (1 instancia para toda la app) | Scoped (1 por request) | Transient (1 por llamada)
Detectar: ⚠️ Singleton que depende de Scoped → captive dependency bug

### 7.3.3 Eventos / Mensajes Internos (si aplica)
Si hay un bus de eventos, MediatR notifications, o similar:

  | Evento/Notificación | Publicado por | Manejado por | Trigger | Archivo |
  |---|---|---|---|---|
  | UserCreatedEvent | CreateUserHandler | EmailHandler, AuditHandler | Registro exitoso | UserCreatedEvent.cs |

## 7.4 Integraciones Externas

Por cada servicio externo detectado (API, DB externa, storage, auth provider, etc.):

### [Nombre del servicio externo]
  **Tipo:** REST API | SOAP | SDK | DB Connection | Message Broker | OAuth Provider
  **¿Qué es este servicio?** definición breve
  **¿Qué hace la app con él?** acción concreta (envía emails, valida tokens, guarda archivos, etc.)
  **Configuración detectada:** URL base, timeouts, retry policy → `archivo:línea`
  **Autenticación usada:** API Key / OAuth / Basic / mTLS → `archivo:línea`
  **Contrato de datos:** qué se envía y qué se recibe (estructura)
  **Resiliencia implementada:** ¿hay retry? ¿circuit breaker? ¿fallback? → `archivo:línea` o [AUSENTE]
  **Impacto si falla:** qué parte del sistema queda degradada/rota

## 7.5 Flujo de Datos Persistentes (DB)

### 7.5.1 Mapa de Entidades y Relaciones
Diagrama ER en Mermaid de las tablas/colecciones detectadas:

  erDiagram
    USERS ||--o{ ORDERS : "tiene"
    ORDERS ||--|{ ORDER_ITEMS : "contiene"
    ...

### 7.5.2 Operaciones de Lectura vs Escritura
Clasificar cada repositorio/query:

  | Operación | Tipo | Repositorio | Query generada (resumen) | Índice usado / faltante |
  |---|---|---|---|---|
  | GetUserById | Lectura | UserRepo.cs:45 | SELECT * WHERE Id=? | PK_Users ✅ |
  | CreateOrder | Escritura | OrderRepo.cs:78 | INSERT + relacionados | — |
  | GetOrdersByUser | Lectura | OrderRepo.cs:92 | SELECT WHERE UserId=? | ⚠️ Sin índice en UserId |

### 7.5.3 Transacciones
Detectar dónde se usan transacciones explícitas:
  - `archivo:línea` → qué operaciones agrupa → qué pasa si falla a mitad

## 7.6 Middleware Pipeline

Orden exacto de los middlewares detectados (crítico — el orden importa):

  Request entra
    ↓ 1. [NombreMiddleware] — qué hace — `archivo:línea`
    ↓ 2. [NombreMiddleware] — qué hace — `archivo:línea`
    ↓ 3. [NombreMiddleware] — qué hace — `archivo:línea`
    ↓ N. Router → Controller
  Response sale
    ↑ N. [en reversa si aplica]

Marcar: ⚠️ si un middleware debería estar antes/después de otro por seguridad o correctness.

## 7.7 Timeouts, Retries y Resiliencia

Tabla de políticas de resiliencia detectadas (o ausentes):

| Conexión | Timeout configurado | Retry | Circuit Breaker | Fallback | Archivo |
|---|---|---|---|---|---|
| DB principal | 30s (conn string) | No | No | 🔴 Sin fallback | appsettings.json |
| API externa | [AUSENTE] 🔴 | No | No | 🔴 Sin fallback | ExternalService.cs:34 |
| Cache | 5s | Sí (3 intentos) | No | Bypass a DB | CacheService.cs:12 |
```

---

### 📄 `/docs/08-Contratos_API_y_Endpoints.md` *(si aplica)*

> Generar solo si el proyecto expone una API HTTP/REST/GraphQL.

```markdown
## 8.1 Tabla de Endpoints
| Método | Ruta | Auth requerida | Request Body | Response exitoso | Errores posibles | Controller:línea |

## 8.2 Modelos de Request/Response
Por cada DTO/ViewModel:
  - Campos: nombre | tipo | obligatorio | validaciones | ejemplo de valor

## 8.3 Autenticación y Autorización
  - Mecanismo (JWT, Cookie, API Key, etc.) + definición del mecanismo
  - Flujo de autenticación (diagrama texto)
  - Roles/Claims detectados y qué protegen
```

---

### 📄 `/docs/09-Deuda_Tecnica_y_Bugs_Conocidos.md`

```markdown
## 9.1 Hallazgos por Severidad

### 🔴 Críticos (bloquean producción o seguridad)
### 🟡 Medios (degradan calidad o mantenibilidad)  
### 🟢 Menores (mejoras cosméticas o de estilo)

Formato por hallazgo:
  **[ID]** `archivo:línea`
  - **Problema:** descripción exacta
  - **Evidencia:** fragmento de código (máx 5 líneas)
  - **Impacto:** consecuencia concreta
  - **Fix sugerido:** pseudocódigo o descripción de solución

## 9.2 Cobertura de Tests
  - ¿Hay tests? (unit / integration / e2e)
  - % estimado de cobertura por capa
  - Flujos críticos SIN test → listar

## 9.3 Health Score
  Puntuación /100 con desglose por dimensión:
  | Dimensión | Puntos | Justificación |
  |-----------|--------|---------------|
  | Arquitectura | /20 | |
  | Seguridad | /20 | |
  | Calidad de código | /20 | |
  | Testing | /20 | |
  | Documentación existente | /10 | |
  | Deploy/Infra | /10 | |
  | **TOTAL** | **/100** | |
```

---

## REGLAS DE EJECUCIÓN

```yaml
orden_de_entrega:
  - Fase 0 (inventario) → confirmar antes de continuar
  - Docs 01 a 09 → uno por respuesta, esperar "continúa" entre cada uno
  - Si el repo es pequeño (<15 archivos) → 01+02 en una respuesta, 03+04, 05+06, 07+08+09

formato_de_código:
  - Fragmentos: máx 10 líneas, siempre con ruta y número de línea
  - Tablas sobre bullets cuando hay ≥3 atributos
  - Mermaid para diagramas si es posible

manejo_de_gaps:
  - Archivo inaccesible → "[INACCESIBLE: ruta]" + skip razonado
  - Comportamiento ambiguo → "[INFERIDO]" + evidencia + ⚠️ requiere confirmación
  - Tecnología desconocida → buscar en package manager antes de documentar

prohibiciones:
  - No inventar endpoints que no estén en el código
  - No asumir que algo "probablemente hace X"
  - No omitir archivos por parecer irrelevantes
```

---

## PROMPT DE INICIO (copiar y pegar)

```
Ejecuta el PROMPT de Auditoría y Documentación Técnica v2.2.

Repo en contexto: [RUTA O DESCRIPCIÓN]

Empieza por FASE 0: genera el inventario completo del repositorio antes de cualquier documento.
Espera mi confirmación antes de generar el primer archivo.
```

---

*Prompt diseñado para proyectos .NET / Clean Architecture / CQRS — adaptable a cualquier stack.*
