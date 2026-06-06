# CLAUDE.md — Registro de cambios del proyecto TFG Privacidad Web

Este fichero tiene dos funciones:
1. Dar contexto completo del proyecto para que cualquier chat nuevo pueda arrancar
   sin necesidad de explicaciones adicionales.
2. Registrar todos los cambios realizados sobre ficheros del proyecto: qué se modificó,
   en qué fichero, por qué y cómo funciona cada cambio.

**Instrucción para el asistente:** actualizar este fichero siempre que se modifique
o cree cualquier fichero del proyecto, antes de dar la tarea por terminada.

---

## Qué es este proyecto

TFG de auditoría de privacidad web. El objetivo es medir una serie de requisitos
de privacidad (R1-R19, basados en RGPD, AEPD y normativa LSSI) sobre sitios web
reales, utilizando varias herramientas combinadas.

Los requisitos están definidos y descritos en detalle en:
- **`/home/pedro/Descargas/PRIVACIDAD EN SITIOS WEB (2).pdf`** — marco teórico,
  definición de cada requisito, normativas relevantes (RGPD, LSSI, AEPD) y tipos
  de cookies.
- **`/home/pedro/Documentos/CROQUIS_REQUISITOS`** — fichero de texto plano con el
  procedimiento concreto de medición de cada requisito: qué herramienta usar, qué
  consultas SQL ejecutar, pseudocódigo de los scripts a desarrollar y rutas de
  ficheros de salida. **Es la referencia principal para implementar cada requisito.**

---

## Objetivo final (visión a largo plazo)

Una vez implementados y verificados individualmente todos los requisitos, se quiere
integrar todo en una **aplicación unificada** con dos modos de uso:

1. **Modo auditoría de un sitio**: dado un sitio web, ejecuta todos los requisitos
   y devuelve un informe detallado de cuáles pasa y cuáles falla.
2. **Modo batch / listado**: dado un CSV de sitios, ejecuta la auditoría sobre todos
   y genera gráficos de cuántos sitios cumplen o no cada requisito, con un resumen
   por sitio.

De momento se está implementando cada requisito por separado de forma funcional y
demostrable antes de integrar.

---

## Arquitectura de la herramienta unificada (decisiones de diseño)

### Gestión del espacio en disco — modo batch

Al auditar múltiples sitios (ej. 100), guardar la salida raw de cada herramienta
por cada sitio consumiría un espacio inasumible:

| Tool | Salida principal | Tamaño aprox. por sitio |
|---|---|---|
| WEC | `requests.har` | 10–50 MB |
| OpenWPM | `crawl-data.sqlite` | 50–200 MB |
| PoliGraph | JSONs + grafo YML | 2–5 MB |
| Blacklight | `inspection.json` | 1–3 MB |

Para 100 sitios, OpenWPM y WEC solos pueden superar fácilmente los 10–20 GB.

**Decisión: procesamiento en streaming con descarte inmediato del raw.**

Para cada sitio, el flujo es:
```
sitio → lanzar herramienta → ejecutar script de análisis → guardar JSON resultado → borrar salida raw → siguiente sitio
```

Al finalizar, solo se conservan los JSONs de resultados (unos pocos KB por requisito
por sitio) y un fichero acumulador único con todos los resultados.

### Fichero acumulador de resultados

El resultado final del modo batch es un único JSON estructurado por sitio:

```json
{
  "fecha": "2026-06-06",
  "sitios": {
    "decathlon.es": {
      "r1":  { "veredicto": "PASSED", "detalle": "..." },
      "r4":  { "veredicto": "PASSED", "categorias": ["Cookies publicitarias", ...] },
      "r5":  { "veredicto": "PASSED" }
    },
    "elpais.com": { ... }
  }
}
```

### Excepción: tools con BD compartida entre sitios

Dos tools no encajan en el modelo de streaming sitio a sitio porque generan una
única base de datos para todos los sitios del crawl:

- **OpenWPM**: lanza un único crawl sobre todos los sitios y genera una sola
  `crawl-data.sqlite`. El flujo correcto es: crawl completo de N sitios →
  ejecutar todos los scripts de análisis sobre esa BD → borrar la BD.
- **Privacy Pioneer + MySQL**: la BD acumula datos de todos los sitios visitados.
  El flujo es: crawl completo → análisis completo → limpiar las tablas.

Para el resto (PoliGraph, WEC, Blacklight), el streaming sitio a sitio funciona
sin ningún cambio.

### Implicación para los scripts actuales

Los scripts individuales **ya están preparados** para la integración:
- Aceptan la ruta del directorio de salida de la herramienta como argumento CLI.
- Devuelven un JSON pequeño en `analysis_data/`.
- La ruta por defecto hardcodeada a `elpais_audit` es solo para pruebas en desarrollo;
  la herramienta unificada siempre pasará la ruta correcta del sitio que está auditando.

El orquestador de la herramienta unificada es quien:
1. Pasa a cada script la ruta del output del sitio actual.
2. Lee el JSON que genera el script.
3. Lo inserta en el acumulador.
4. Borra el directorio raw del sitio.

---

## Herramientas disponibles y sus rutas

Todas las herramientas están en `/home/pedro/Escritorio/UNI/CUARTO/tfg/`:

| Herramienta | Ruta | Requisitos que cubre |
|---|---|---|
| **Privacy Pioneer** (crawler) | `privacy-pioneer-web-crawler/` | R2, R3, R9, R12, R15 |
| **WEC** (Website Evidence Collector) | `WEC/` | R10, R17, R18 |
| **webXray** | `webXray/` | R12 (combinado con PP), R15, R16 |
| **PoliGraph** | `PoliGraph/` | R1, R14, R15, R16, R19 (con traducción automática) |
| **OpenWPM** | `openWPM/` | R6, R7, R8, R11 |
| **Blacklight** | `BL/` | R6, R7, R11 (contraste con OpenWPM) |
| **Firefox Nightly** | `firefox/` | Navegador usado por Privacy Pioneer |
| **Playwright (Python)** | librería pip | R13 (dark patterns, análisis de banner) |

Ficheros de salida de análisis: `privacy-pioneer-web-crawler/analysis_data/`

Scripts de análisis desarrollados: `privacy-pioneer-web-crawler/analysis_scripts/`

---

## Estado actual de los requisitos

### Implementados y funcionales

| Requisito | Herramienta(s) | Script | Estado |
|---|---|---|---|
| R2 (Cookies no exentas) | Privacy Pioneer | `analysis_scripts/r2_r3_cookies_beacons.py` | ✅ Funcional |
| R3 (Web Beacons) | Privacy Pioneer | Cubierto por R2 (`trackingPixel` en PRE) → mismo script | ✅ Funcional |
| R9 (Minimización de datos) | Privacy Pioneer | `analysis_scripts/r9_minimizacion.py` | ✅ Funcional |
| R7 (Fingerprinting) | OpenWPM | `analysis_scripts/r7_fingerprinting.py` | ✅ Funcional |
| R11 (Desvinculación y Aislamiento) | OpenWPM | `analysis_scripts/r11_desvinculacion.py` | ✅ Funcional |
| R10 (Limitación del plazo) | WEC | `analysis_scripts/r10_persistencia.py` | ✅ Funcional |
| R17 (Redirección HTTPS) | WEC + petición en vivo | `analysis_scripts/r17_r18_seguridad.py` | ✅ Funcional |
| R18 (Content Security Policy) | WEC | `analysis_scripts/r17_r18_seguridad.py` | ✅ Funcional |
| R12 (Software de terceros) | Privacy Pioneer + webXray | `analysis_scripts/r12_software_terceros.py` | ✅ Funcional |
| R15 (Identificación de responsables) | Privacy Pioneer + OCD + PoliGraph | `analysis_scripts/r15_responsables.py` | ✅ Funcional |
| R6 (Anti-Keylogging) | Blacklight | `analysis_scripts/r6_keylogging.py` | ✅ Funcional |
| R8 (Storage de terceros) | OpenWPM | `analysis_scripts/r8_storage_terceros.py` | ✅ Funcional |
| R13 (Dark Patterns) | Playwright (Python) | `analysis_scripts/r13_dark_patterns.py` | ✅ Funcional |
| R19 (Designación y contacto del DPO) | PoliGraph | `analysis_scripts/r19_dpo.py` | ✅ Funcional |
| R1 (Información por capas) | PoliGraph | `analysis_scripts/r1_capas.py` | ✅ Funcional |
| R14 (Lenguaje claro y sencillo) | PoliGraph | `analysis_scripts/r14_lenguaje.py` | ✅ Funcional |
| R4 (Granularidad en la elección) | Playwright (Node.js) | `analysis_scripts/r4_granularidad.js` | ✅ Funcional |
| R5 (Revocabilidad sencilla) | PoliGraph | `analysis_scripts/r5_revocabilidad.py` | ✅ Funcional |
| R16 (Correspondencia aviso-ejecución) | PoliGraph + webXray + DDG | `analysis_scripts/r16_correspondencia.py` | ✅ Funcional |

### Pendientes de implementar

Ninguno — todos los requisitos R1-R19 están implementados.

El orden de implementación siguió el documento `CROQUIS_REQUISITOS`. El bloque WEC
(R10, R17, R18) está completo. El bloque OpenWPM/Blacklight (R6, R7, R8, R11) está completo.
El bloque PoliGraph (R15, R19, R1, R14, R16) está completo. R4 y R5 se implementaron como apartado separado.

---

## Decisiones de diseño

Esta sección documenta decisiones técnicas no obvias tomadas durante el desarrollo,
con su justificación, para que queden reflejadas en el documento final del TFG.

---

### R13 — Elección de Python + Playwright frente a otras alternativas

**Contexto:**
R13 requiere analizar el banner de cookies de un sitio web en vivo: detectar si existe,
leer el color y texto de sus botones, y verificar si se aplican dark patterns visuales
(asimetría cromática, ausencia de botón de rechazo en primera capa, etc.). Para ello
es imprescindible un navegador headless que renderice la página completa con JavaScript.

**Alternativas consideradas:**

| Alternativa | Pros | Contras |
|---|---|---|
| **Node.js + Puppeteer** | Mencionado en el croquis; coherente con `local-crawler.js` | Rompe el ecosistema Python del resto de scripts; el resultado JSON requeriría un paso extra de integración |
| **Python + Selenium** | Ya presente en el proyecto (crawler de PP) | API de espera más rígida; soporte de Shadow DOM limitado sin código adicional |
| **Python + Playwright** | Todo el proyecto es Python; API moderna con `wait_for_selector`; soporte nativo de Shadow DOM e iframes | Dependencia nueva (`pip install playwright`) |

**Decisión: Python + Playwright.**

**Limitación conocida — Bloqueo por IP (DataDome y similares):**
Algunos sitios (elpais.com, entre otros) usan DataDome, un servicio de protección
anti-bot que opera en dos fases: (1) reputación de IP antes de servir la página y
(2) fingerprinting JS una vez cargada. Las técnicas de stealth implementadas
(`playwright-stealth`, `--disable-blink-features=AutomationControlled`, parches de
`navigator.webdriver`, `window.chrome` y `navigator.plugins`) resuelven la fase 2.
La fase 1 es exclusivamente de red: si la IP de origen está en la lista negra de
DataDome (por actividad previa de otros usuarios en la misma red), la página de
bloqueo se sirve antes de ejecutar ningún JavaScript. La solución es cambiar de
conexión (datos móviles, VPN con IP residencial). Esta limitación debe mencionarse
en el documento final como restricción del entorno de pruebas.

**Justificación técnica:**

El argumento inicial era que el lenguaje del frontend de cada sitio (React, Angular, Vue,
HTML estático) podría condicionar la elección de la herramienta. Sin embargo, esto es un
falso problema: cualquier navegador headless interactúa con el **DOM renderizado final**,
que siempre es HTML independientemente del framework. Lo que sí varía y exige cuidado son:

1. **Timing de renderizado**: los banners en SPAs (React, Angular) aparecen de forma
   asíncrona, décimas de segundo después de la carga. Playwright resuelve esto con
   `wait_for_selector` y timeouts configurables, evitando carreras de condición.

2. **Shadow DOM**: las plataformas CMP más extendidas (OneTrust, Cookiebot, Didomi)
   renderizan su banner dentro de un Shadow Root, invisible para `querySelector` estándar.
   Playwright tiene soporte nativo para atravesar Shadow DOM sin código adicional.

3. **iframes**: algunos banners se cargan en un contexto de iframe separado. Playwright
   permite cambiar el contexto de ejecución al iframe con `frame_locator`.

La elección de Playwright sobre Selenium responde a estos tres puntos. La elección de
Python sobre Node.js responde a la coherencia del proyecto: todos los scripts de análisis
(R6, R7, R8, R9, R10, R11, R12, R15, R17, R18) son Python y guardan su resultado en
`analysis_data/` con el mismo formato JSON, lo que facilitará la integración final.

---

### PoliGraph — Modificación para soporte multilingüe (traducción automática)

**Contexto:**
PoliGraph está diseñado para analizar políticas de privacidad en inglés. El modelo NLP
que extrae entidades, relaciones y actores del grafo fue entrenado sobre corpus en inglés.
Para sitios web españoles, el análisis sin modificar producía resultados degradados:
extracción de categorías genéricas ("payment gateway", "sector of activity") en lugar
de nombres de empresa reales.

**Modificación implementada:**
Se añadió traducción automática al pipeline de procesamiento de PoliGraph en dos ficheros:

- **`PoliGraph/poligrapher/translator_utils.py`** (fichero nuevo, con protección de entidades):
  Implementa un sistema de protección por placeholders antes de llamar al traductor.
  Antes de enviar el texto a Google Translate se sustituyen las entidades sensibles por
  marcadores `__ENT0__`, `__ENT1__`... que el traductor interpreta como variables de
  código y deja intactos. Al finalizar la traducción se restauran los valores originales.

  Patrones protegidos (en orden de aplicación, de más específico a más general):
  1. URLs (`https://...`, `www....`)
  2. Emails completos — especialmente su parte local (`privacidad@`, `lopd@`)
  3. Nombres de empresa con forma jurídica (`S.A.`, `S.L.`, `S.L.U.`, `B.V.`, `GmbH`...)
     — hasta 6 palabras antes del sufijo legal, para capturar nombres como
     `"Datos Seguros Iberia, S.A."` cuya primera palabra es un sustantivo común
  4. Siglas de 2-8 letras mayúsculas (red de seguridad: Google ya las preserva
     para las conocidas, pero esto garantiza consistencia para las locales)

  Problema resuelto: sin protección, `privacidad@empresa.es` → `privacy@empresa.es`
  y `"Datos Seguros Iberia, S.A."` → `"Data Seguros Iberia, S.A."`.
  Con protección, ambas formas se preservan exactas.

- **`PoliGraph/poligrapher/document.py`** (modificado, línea ~337):
  Justo antes de que el texto se pase al modelo NLP, se añade la comprobación:
  ```python
  if not detect_english(inner_text):        # langdetect (ya existía en el fichero)
      logging.info("Texto no inglés detectado. Traduciendo...")
      inner_text = translate_policy_text(inner_text)
  ```
  `detect_english()` ya existía en `document.py` y usa `langdetect`. La nueva llamada
  a `translate_policy_text()` se inserta únicamente cuando el texto NO es inglés,
  evitando latencia innecesaria en políticas ya en inglés.

**Resultado:**
Con esta modificación, PoliGraph traduce cada párrafo de la política al inglés antes
de pasarlo al modelo, preservando emails, URLs, formas jurídicas y nombres de empresa.
Esto permite extraer actores y relaciones con mayor precisión para sitios en español.

**Dependencia:** `pip install deep-translator` (en el entorno virtual de PoliGraph).

---

## Arquitectura de Privacy Pioneer (ya modificada)

El flujo completo es:

```
Selenium (local-crawler.js)
  → abre Firefox con extensión Privacy Pioneer (.xpi)
  → visita sitio web
  → espera 15s (fase PRE) → captura cookies PRE
  → intenta clicar banner de consentimiento
  → notifica a REST API (/notificar-clic) → marca timestamp de consentimiento
  → espera resto del tiempo (fase POST) → captura cookies POST
  → guarda cookies en analysis_data/reporte_auditoria.json

Privacy Pioneer (extensión)
  → intercepta peticiones de red
  → detecta rastreadores y los clasifica por tipo
  → envía evidencias a REST API (/entries)

REST API (rest-api/index.js)
  → recibe evidencias
  → asigna consent_phase = 'PRE' o 'POST' según timestamp del clic
  → inserta en MySQL tabla 'entries'
```

**Tabla MySQL `entries` — campos relevantes:**
- `rootUrl` — sitio web visitado
- `requestUrl` — URL de la petición del rastreador
- `typ` — tipo: advertising, analytics, social, trackingPixel, fingerprinting, ipAddress, region, city
- `parentCompany` — empresa propietaria del rastreador
- `snippet` — fragmento del contenido de la petición/respuesta (datos personales detectados)
- `consent_phase` — 'PRE' o 'POST'

**Credenciales MySQL:** usuario `pioneer`, password `abc`, base de datos `analysis`

---

## Contexto general del proyecto

TFG de auditoría de privacidad web. El objetivo es medir una serie de requisitos
(R1-R19) sobre sitios web reales utilizando varias herramientas combinadas:

- **Privacy Pioneer** (extensión Firefox): detecta rastreadores y captura evidencias,
  las almacena en MySQL (tabla `entries`, usuario `pioneer`, password `abc`, BD `analysis`).
- **Selenium** (`selenium-crawler/local-crawler.js`): automatiza la visita a sitios web,
  interactúa con banners de cookies y captura cookies en dos fases: PRE (antes de
  consentimiento) y POST (después de consentimiento).
- **REST API** (`rest-api/index.js`): recibe los datos de Privacy Pioneer y los inserta
  en MySQL, asignando la fase PRE/POST según si hubo clic en el banner antes de la petición.
- **Scripts de análisis** (`analysis_scripts/`): scripts Python que leen la BD y los
  ficheros de salida de otras herramientas para evaluar cada requisito.

---

## Cambios en ficheros existentes

---

### `selenium-crawler/local-crawler.js`

#### Cambio 1 — Limpieza de cookies entre visitas (línea 308)

**Por qué:**
Al crawlear varios sitios en la misma sesión de Firefox, las cookies de terceros
(Google, DoubleClick, etc.) puestas por el sitio A persistían en el navegador al
visitar el sitio B. `driver.manage().getCookies()` devolvía esas cookies residuales
mezcladas con las del nuevo sitio, contaminando los datos de PRE y POST.

**Cómo:**
Se añade una llamada a `driver.manage().deleteAllCookies()` justo antes de
`driver.get(url)`, al inicio de cada visita. Esto vacía completamente el almacén
de cookies del navegador, garantizando que cada visita empieza desde cero.

```javascript
await driver.manage().deleteAllCookies();  // ← añadido
await driver.get(url);
```

---

#### Cambio 2 — Nueva clase `DriverCrashError` (líneas 77-82)

**Por qué:**
Se necesitaba un tipo de error específico para distinguir los crashes de Firefox
de otros errores críticos, permitiendo mensajes de log más claros y un tratamiento
diferenciado en el catch exterior.

**Cómo:**
Se añade una clase de error personalizada, igual en estructura a la ya existente
`HumanCheckError`:

```javascript
class DriverCrashError extends Error {
  constructor(message) {
    super(message);
    this.name = "DriverCrashError";
  }
}
```

---

#### Cambio 3 — Manejo limpio del crash de Firefox en POST (líneas 419-424 y 468-472)

**Por qué:**
Firefox Nightly se cuelga ocasionalmente durante la fase POST en sitios con carga
pesada de scripts publicitarios (ej: marca.com). El flujo anterior producía dos
errores en cascada confusos:
1. `Failed to decode response from marionette` — capturado silenciosamente
2. `Tried to run command without establishing a connection` — del `driver.getTitle()`
   siguiente, que era el que realmente disparaba el reinicio

El resultado era correcto (reinicio del driver) pero el log era engañoso y el
código seguía llamando a un driver muerto.

**Cómo:**
En el catch de la captura de cookies POST, si el mensaje contiene `marionette` o
`connection` (palabras que indican que Firefox murió), se lanza inmediatamente un
`DriverCrashError` en vez de continuar:

```javascript
} catch (errCookie) {
  if (/marionette|connection/i.test(errCookie.message)) {
    throw new DriverCrashError(`Firefox caído en POST: ${errCookie.message}`);
  }
  console.error("⚠️ Error al extraer cookies en POST:", errCookie.message);
}
```

En el catch exterior se diferencia el mensaje de log:

```javascript
if (e.name === "DriverCrashError") {
  console.log("------ Firefox crasheó en POST, reiniciando driver ------");
} else if (e.name != "HumanCheckError") {
  console.log("------ Reiniciando driver por error crítico ------");
}
```

El reinicio sigue ocurriendo igual en ambos casos (porque ninguno es `HumanCheckError`).

---

## Ficheros nuevos creados

---

### `analysis_scripts/r7_fingerprinting.py`

**Requisito que implementa:** R7 — Protección contra Fingerprinting (RGPD Art. 5 / AEPD)

**Por qué:**
Detectar si scripts de terceros recogen señales del navegador y del dispositivo
para construir una huella digital del usuario sin su consentimiento. El fingerprinting
permite identificar y rastrear al usuario aunque borre sus cookies, porque la huella
se basa en características del hardware y el software que no cambian entre sesiones.

**Fuente de datos:** tabla `javascript` de la BD SQLite de OpenWPM.

**Sistema de puntuación (solo scripts de terceros):**

*Capa 1 — peso por símbolo individual:*
- Peso 3: `measureText`, `navigator.plugins`, `navigator.mimeTypes`, `navigator.oscpu`
- Peso 2: `navigator.buildID`, `navigator.hardwareConcurrency`, `navigator.platform`, `screen.colorDepth`, `screen.pixelDepth`
- Peso 1: `getContext`, `navigator.vendor`, `navigator.appVersion/appName`, `navigator.languages`, `navigator.maxTouchPoints`, `navigator.userAgent`

*Capa 2 — bonus por combinación:*
- +3 si el mismo script usa `getContext` + `measureText` (font fingerprint confirmado)
- +2 si el mismo script accede a ≥ 4 señales de navigator distintas (batería completa)

*Umbrales:*
- Puntuación ≥ 7 → FAILED (fingerprinting confirmado)
- Puntuación ≥ 3 → WARNING (indicios claros)
- Sin scripts ≥ 3 → PASSED

**Deduplicación:** un mismo script puede cargarse desde varias páginas del sitio;
se conserva únicamente la entrada con la puntuación más alta.

**Uso:**
```bash
python3 r7_fingerprinting.py                              # BD por defecto
python3 r7_fingerprinting.py /ruta/crawl-data.sqlite      # BD explícita
python3 r7_fingerprinting.py --no-detalle                 # solo resumen
```

---

### `analysis_scripts/r11_desvinculacion.py`

**Requisito que implementa:** R11 — Desvinculación y Aislamiento (RGPD Art. 5.1.f / Considerando 26)

**Por qué:**
Detectar si plataformas de terceros intercambian identificadores de usuario entre sí
(cookie syncing) para correlacionar perfiles procedentes de distintas fuentes, vulnerando
el principio de independencia de contextos de tratamiento.

**Fuente de datos:** tabla `http_requests` de la BD SQLite de OpenWPM, campo `triggering_origin`.

**Cómo funciona:**
1. Carga todas las peticiones HTTP donde `triggering_origin` está definido (indica qué dominio
   disparó la petición).
2. Aplica patrones de URL sobre cada petición para detectar endpoints de cookie sync:
   `/ibs:` (Adobe ID Bridge), `getUID`, `UCookieSetPug` (PubMatic), `/sync`, `user_sync`,
   `dpuuid=`, `uid=`, `guid=`, `/match`, `syncframe`, `cookie_sync`, `/rd?` (Demdex redirect).
3. Clasifica cada evento de sync:
   - **Tercero→Tercero**: trigger ≠ sitio y trigger ≠ receptor → FAILED (correlación directa)
   - **Sitio→Tercero**: el propio sitio inicia el sync → WARNING
4. Construye un grafo de sincronización con conteo de eventos por pareja `(origen→destino)`.

**Umbrales:**
- ≥ 1 evento tercero→tercero → FAILED
- Solo syncs sitio→tercero → WARNING
- Sin syncs detectados → PASSED

**Nota:** la extracción de dominio base puede producir falsos positivos en TLDs de dos
niveles (`.com.tr`, `.co.uk`). No afecta al veredicto global.

**Uso:**
```bash
python3 r11_desvinculacion.py                          # BD por defecto
python3 r11_desvinculacion.py /ruta/crawl-data.sqlite  # BD explícita
python3 r11_desvinculacion.py --no-detalle             # solo resumen
```

---

### `analysis_scripts/r9_minimizacion.py`

**Requisito que implementa:** R9 — Minimización de datos (RGPD Art. 5.1.c)

**Por qué:**
Verificar que los sitios web no recogen datos personales excesivos, especialmente
antes del consentimiento del usuario (fase PRE). La violación del principio de
minimización implica que se recogen más datos de los estrictamente necesarios.

**Fuente de datos:** tabla `entries` de MySQL, campo `snippet`. Este campo contiene
el contenido real de las peticiones/respuestas interceptadas por Privacy Pioneer.

**Cómo funciona:**
1. Consulta MySQL para obtener todos los snippets no vacíos, agrupados por sitio y fase.
2. Aplica 9 patrones regex sobre cada snippet para detectar categorías de datos personales:
   - 🔴 Alto riesgo: `IP_ADDRESS`, `EMAIL`, `TELEFONO`, `COORDENADAS`, `CANVAS_FINGERPRINT`
   - 🟡 Riesgo medio: `GEOLOCALIZACION`, `ISP_INFO`, `USER_AGENT`, `DISPOSITIVO_HW`, `USER_ID`
3. Evalúa R9 por sitio:
   - `FALLO` — datos de alto riesgo en PRE (sin consentimiento)
   - `ADVERTENCIA` — datos de riesgo medio en PRE
   - `OK` — datos solo en POST (tras consentimiento)
   - `SIN_DATOS` — sin snippets que analizar
4. Guarda resultado en `analysis_data/r9_resultado.json`.

**Uso:**
```bash
python3 r9_minimizacion.py              # todos los sitios
python3 r9_minimizacion.py abc.es       # filtrar por sitio
```

---

### `analysis_scripts/r15_responsables.py`

**Requisito que implementa:** R15 — Identificación de Responsables (RGPD Art. 13/14)

**Por qué:**
Verificar que cada cookie recogida tiene un propietario identificable y que el
propio sitio web identifica claramente a su responsable del tratamiento en la
política de privacidad.

**Fuentes de datos:**
- `analysis_data/reporte_auditoria.json` — cookies capturadas por Selenium (PRE y POST)
- Open Cookie Database (JSON instalado como extensión Chrome) — 2.196 cookies conocidas
- MySQL tabla `entries` — `parentCompany` por dominio (fallback)
- PoliGraph YML (opcional) — grafo de la política de privacidad

**Cómo funciona:**

*Identificación de propietario por cookie (cascada de 3 fuentes):*
1. **Open Cookie Database**: búsqueda por nombre exacto y por wildcard (`AMCVS_*`, `_ga_*`...)
2. **Privacy Pioneer DB**: si el dominio de la cookie coincide con un `requestUrl` ya
   analizado, se usa su `parentCompany`
3. **Dominio propio**: si el dominio de la cookie pertenece al propio sitio (`abc.es`,
   `marca.com`), se marca como `"Sitio propio"` — no cuenta como no identificada
4. `"No identificado"` si ninguna fuente lo resuelve

*Evaluación del estado por fase:*
- `PASS` — ninguna cookie queda como `"No identificado"`
- `FAIL` — al menos una cookie de tercero sin propietario conocido

*Verificación PoliGraph (opcional, a nivel de sitio):*
Sigue la cadena SUBSUM desde los nodos `we`/`joint controller`/`vgms` del grafo
para encontrar la entidad legal responsable. Verifica si tiene NIF/CIF y dirección.
- `PASS` — responsable identificado con datos suficientes
- `ADVERTENCIA` — responsable mencionado pero sin NIF ni dirección
- `FAIL` — no se identificó ningún responsable

**Uso:**
```bash
python3 r15_responsables.py                          # solo identificación de cookies
python3 r15_responsables.py /ruta/graph.full.yml     # + verificación responsable en política
python3 r15_responsables.py /ruta/graph.full.yml --no-detalle  # solo resumen
```

---

### `analysis_scripts/r17_r18_seguridad.py`

**Requisitos que implementa:** R17 — Redirección HTTPS / R18 — Content Security Policy (RGPD Art. 32)

**Fuente de datos:** `requests.har` generado por WEC + petición HTTP en vivo (solo R17).

**R17 — Lógica:**
- Petición en vivo a `http://{sitio}/` con `allow_redirects=False`:
  - 301 → PASSED (permanente)
  - 302/307/308 → WARNING (temporal, debería ser 301)
  - 403 → WARNING (posible WAF, requiere verificación manual)
  - 200 → FAILED (sirve HTTP directamente)
  - Puerto 80 rechazado → PASSED (bloquea HTTP)
- Complemento HSTS desde cabeceras del documento HAR:
  - `max-age` ≥ 31.536.000s + `includeSubDomains` → PASSED
  - Parcial o insuficiente → WARNING
  - Ausente → WARNING (primera visita vulnerable a MITM)
- Veredicto: el peor de redirect + HSTS.

**R18 — Lógica:**
- Sin CSP ni CSP-Report-Only → FAILED
- Solo `Content-Security-Policy-Report-Only` → WARNING (no se aplica)
- CSP presente → analizar:
  - `unsafe-inline` / `unsafe-eval` en `script-src`/`default-src` → WARNING
  - Wildcard `*` en directivas de script → WARNING
  - Sin `script-src` ni `default-src` → WARNING (CSP incompleta)
  - Sin `frame-ancestors` → WARNING (clickjacking posible)
  - Sin advertencias → PASSED

**Uso:**
```bash
python3 r17_r18_seguridad.py                        # HAR por defecto
python3 r17_r18_seguridad.py /ruta/wec/output/      # directorio WEC explícito
python3 r17_r18_seguridad.py /ruta/requests.har     # fichero HAR directo
python3 r17_r18_seguridad.py --no-detalle           # solo resumen
```

---

### `analysis_scripts/r10_persistencia.py`

**Requisito que implementa:** R10 — Limitación del plazo de persistencia (RGPD Art. 5.1.e)

**Por qué:**
Verificar que las cookies persistentes tienen una caducidad proporcional a su
finalidad declarada, evitando plazos excesivos de años sin justificación.

**Fuentes de datos:**
- `cookies.yml` (WEC) — lista de cookies con `expiresDays` y `firstPartyStorage`
- `local-storage.yml` (WEC) — items de LocalStorage (sin expiración por diseño)
- Open Cookie Database — categoría declarada de cada cookie (publicidad, analítica, funcional...)

**Jerarquía de evaluación (por cookie):**
1. `session: true` o sin fecha → PASSED (sin caducidad, no aplica)
2. `expiresDays < 0` → PASSED (ya expirada)
3. Cookie CMP/consent (Didomi, OneTrust, CookieBot, FCCDCF...) → PASSED si ≤ 395 días / WARNING si > 395
4. 3ª parte — publicidad/tracking: > 90 días → FAILED
4. 3ª parte — analítica: > 365 días → FAILED
4. 3ª parte — sin categoría OCD: > 365 días → FAILED
5. 1ª parte — publicidad: > 180 días → WARNING
5. 1ª parte — cualquier categoría: > 395 días → WARNING
- LocalStorage escrito por script de tercero → FAILED (persistencia indefinida)

**Detección de consent cookies:**
Por patrones de nombre: `didomi`, `optanon`, `consent`, `gdpr`, `tcf`, `cookiebot`,
`trustarc`, `euconsent`, `quantcast`, `usercentrics`, `cmapi`, y nombre exacto `FCCDCF` (IAB TCF).

**Uso:**
```bash
python3 r10_persistencia.py                         # directorio WEC por defecto
python3 r10_persistencia.py /ruta/wec/output/       # directorio WEC explícito
python3 r10_persistencia.py /ruta/cookies.yml       # fichero directo
python3 r10_persistencia.py --no-detalle            # solo resumen
```

---

### `analysis_scripts/r12_software_terceros.py`

**Requisito que implementa:** R12 — Software de Terceros (Privacy by Design / RGPD)
"El responsable debe asegurarse de que las funciones de software comercial o de
terceros que no tengan base jurídica estén desactivadas por defecto."

**Por qué:**
Detectar si software de terceros de tipo comercial (publicidad, analítica, redes
sociales, píxeles de seguimiento, fingerprinting) está activo ANTES de que el
usuario dé su consentimiento (fase PRE). Su mera presencia en PRE es la violación.

**Fuentes de datos:**
- MySQL tabla `entries` — entradas en fase PRE con tipos comerciales
- webXray `3p_domains.csv` — dominios de terceros conocidos con propietario y país

**Tipos que se consideran violación en PRE:**
| Tipo PP          | Descripción                      |
|------------------|----------------------------------|
| `advertising`    | Publicidad comportamental        |
| `analytics`      | Analítica de comportamiento      |
| `social`         | Redes sociales                   |
| `trackingPixel`  | Píxel de seguimiento             |
| `fingerprinting` | Fingerprinting de dispositivo    |

**Cómo funciona:**
1. Carga webXray `3p_domains.csv` en un diccionario `{dominio → {owner, país, lineage}}`.
2. Consulta MySQL filtrando `consent_phase = 'PRE'` y los tipos comerciales.
3. Para cada entrada: extrae el dominio del `requestUrl` y lo busca en el mapa de
   webXray (exacto primero, luego por sufijo de subdominio).
4. Construye una "violación" con todos los datos, marcando `en_webxray: True/False`.
5. Deduplica por `(dominio, tipo)` para que el mismo rastreador no aparezca N veces.
6. Evalúa:
   - `FALLO` — si existe al menos una violación
   - `PASS` — si no se detectó ningún rastreador comercial en PRE
7. Distingue entre 🔴 confirmadas por ambas herramientas y 🟡 detectadas solo por PP.
8. Guarda resultado en `analysis_data/r12_resultado.json`.

**Uso:**
```bash
python3 r12_software_terceros.py                        # todos los sitios
python3 r12_software_terceros.py abc.es                 # filtrar por sitio
python3 r12_software_terceros.py /ruta/3p_domains.csv   # usar otro CSV de webXray
python3 r12_software_terceros.py --no-detalle           # solo resumen
```

---

### `analysis_scripts/r6_keylogging.py`

**Requisito que implementa:** R6 — Confidencialidad de las comunicaciones / Anti-Keylogging (RGPD Art. 5.1.f / LSSI Art. 12)

**Por qué:**
El RGPD exige que el software de un sitio no monitorice proactivamente las pulsaciones
de teclado ni los movimientos de ratón del usuario sin base legal. Esta vigilancia
permite perfilar comportamientos de forma encubierta.

**Fuente de datos:** `inspection.json` generado por Blacklight.

**Secciones del JSON analizadas:**
- `reports.key_logging` — keyloggers detectados explícitamente por Blacklight (ej: FullStory capturando POST de formularios)
- `reports.session_recorders` — grabadores de sesión (FullStory, Hotjar, LogRocket…)
- `reports.behaviour_event_listeners.KEYBOARD` — scripts con listeners `keydown`/`keyup`/`keypress`
- `reports.behaviour_event_listeners.MOUSE` — scripts con listeners de movimiento de ratón
- `reports.behaviour_event_listeners.TOUCH` — scripts con listeners táctiles

**Clasificación de severidad (solo scripts de terceros):**
| Condición | Nivel |
|---|---|
| `key_logging` no vacío | FAILED |
| `session_recorders` no vacío | FAILED |
| Listener KEYBOARD de tercero | FAILED |
| Listener mousemove/mousedown/mouseup de tercero | WARNING |
| Listener touchmove/touchstart/touchend de tercero | WARNING |
| Solo scroll/click de tercero | INFO (no cambia veredicto) |

**Determinación de tercero:** comparación de dominio registrable entre URL del script y URL del sitio (`dominio_base` sobre `uri_ins`).

**Veredicto global:**
- `FAILED` — al menos una condición FAILED
- `WARNING` — solo condiciones WARNING (y ninguna FAILED)
- `PASSED` — no se detecta monitorización de terceros relevante

**Uso:**
```bash
python3 r6_keylogging.py                          # JSON por defecto (demo-dir/elpais)
python3 r6_keylogging.py /ruta/inspection.json    # fichero directo
python3 r6_keylogging.py /ruta/directorio/        # directorio con inspection.json
python3 r6_keylogging.py --no-detalle             # solo resumen
```

---

### `analysis_scripts/r8_storage_terceros.py`

**Requisito que implementa:** R8 — Integridad del dispositivo / Accesos no autorizados a memoria (RGPD Art. 5.1.f / Considerando 49)

**Por qué:**
Detectar si scripts de terceros escriben identificadores de rastreo en el almacenamiento
persistente del navegador (localStorage, sessionStorage) sin consentimiento del usuario.
Esto permite seguimiento entre sesiones incluso tras borrar cookies.

**Fuente de datos:** tabla `javascript` de la BD SQLite de OpenWPM.

**Símbolos analizados:**
| Símbolo OpenWPM | Operación | Nivel |
|---|---|---|
| `Storage.setItem` | call / set | FAILED |
| `Storage.getItem` | call | WARNING |
| `Storage.removeItem` | call | WARNING |
| `Storage.key` / `Storage.length` | call / get | WARNING |
| `window.localStorage` | get | WARNING |
| `window.sessionStorage` | get | WARNING |
| `window.navigator.storage` | get | WARNING |
| `window.indexedDB` | get | WARNING |

**Cómo funciona:**
1. Consulta la tabla `javascript` filtrando por símbolos de storage.
2. Descarta scripts de primera parte (mismo dominio registrable que el sitio).
3. Agrupa por `script_url` y acumula los símbolos usados y las claves concretas (extraídas de `arguments`).
4. Clasifica cada script: si tiene al menos un `Storage.setItem` → FAILED; si solo lecturas/accesos → WARNING.
5. Veredicto global: FAILED si hay escrituras de terceros, WARNING si solo lecturas, PASSED si no hay accesos.

**Nota sobre los símbolos:** OpenWPM abstrae localStorage y sessionStorage bajo la misma interfaz `Storage`, por lo que no distingue entre ambos en el nombre del símbolo. La columna `arguments` revela la clave concreta escrita/leída (ej: `["euconsent-v2","..."]`).

**Uso:**
```bash
python3 r8_storage_terceros.py                              # BD por defecto
python3 r8_storage_terceros.py /ruta/crawl-data.sqlite      # BD explícita
python3 r8_storage_terceros.py --no-detalle                 # solo resumen
```

---

### `analysis_scripts/r19_dpo.py`

**Requisito que implementa:** R19 — Designación y datos de contacto del DPO (RGPD Art. 37-39 / Art. 13.1.b)

**Distinción con R15:**
R15 verifica si los propietarios de cookies de **terceros** son identificables cruzando datos
de rastreadores con bases de datos externas (OCD, Privacy Pioneer MySQL). R19 verifica si el
**propio sitio** designa un DPO y publica sus datos de contacto en su política de privacidad,
conforme a RGPD Art. 37.7 (obligación de publicación del contacto del DPO).

**Por qué:**
RGPD Art. 37.7 exige que el responsable del tratamiento publique los datos de contacto del DPO.
Su ausencia en la política de privacidad es una infracción directa, independientemente de si
el DPO existe internamente.

**Fuentes de datos:**
- `readability.json` (PoliGraph) — texto limpio de la política de privacidad (fuente principal)
- `graph-original.full.yml` (PoliGraph) — fragmentos de texto por cláusula (verificación cruzada)

**Por qué readability.json y no el grafo YAML:**
PoliGraph modela el grafo de datos personales (qué se recoge, con quién se comparte, SUBSUM entre
entidades), pero **no modela al DPO como nodo del grafo**. El DPO solo aparece en los cuerpos de
texto de las cláusulas. El grafo sirve para verificación cruzada pero la fuente principal es el
texto completo extraído por el módulo de readability.

**Patrones de detección:**
- Mención: `data protection officer`, `delegado de protección de datos`, `\bDPO\b`, `\bDPD\b` (con `re.IGNORECASE`)
- Email DPO: email con prefijo `dpo@`, `dpd@`, `privacy@`, `privacidad@`, `gdpr@`, `lopd@`, etc.;
  o cualquier email que aparezca en el mismo párrafo que una mención al DPO
- Dirección postal: mención DPO + término de dirección (calle, street, cod. postal...) en un radio de 200 caracteres
- Nombre: búsqueda en dos pasos — trigger DPO (IGNORECASE) → luego `is/es/: [NombreMayúscula]` sin IGNORECASE,
  evitando falsos positivos por palabras genéricas como "through", "whom", "the"

**Veredicto:**
- `PASSED` — DPO mencionado + al menos un método de contacto (email o postal)
- `WARNING` — DPO mencionado pero sin datos de contacto específicos
- `FAILED` — No se detecta mención al DPO en la política

**Uso:**
```bash
python3 r19_dpo.py                                  # directorio PoliGraph por defecto (elpais)
python3 r19_dpo.py /ruta/poligraph/output/          # directorio con readability.json + graph
python3 r19_dpo.py /ruta/readability.json           # fichero readability directo
python3 r19_dpo.py /ruta/readability.json --no-detalle  # solo resumen
```

---

### `analysis_scripts/r1_capas.py`

**Requisito que implementa:** R1 — Información por capas (RGPD Art. 12 / AEPD)

**Por qué:**
RGPD Art. 12 exige que la información sobre el tratamiento de datos sea concisa,
transparente e inteligible, estructurada en capas. La Capa 1 debe ser un resumen
visible e inmediato; la Capa 2 debe ofrecer los detalles técnicos accesibles desde
la Capa 1. Su ausencia implica que el usuario no puede obtener información técnica
sin buscarla activamente.

**Fuente de datos:** `accessibility_tree.json` generado por PoliGraph.

**Cómo funciona:**
1. Recorre el árbol de accesibilidad en profundidad buscando nodos con `role=dialog`
   o `role=alertdialog` cuyo nombre o contenido mencione cookies/privacidad/consentimiento.
2. Si no se encuentra ningún `dialog`, intenta un fallback con `role=region/banner/complementary`.
3. Dentro del banner encontrado (Capa 1), extrae todos los nodos `link` y `button`.
4. Clasifica cada elemento interactivo en:
   - **Nivel A** (acceso técnico): socios/partners, panel de configuración, "aprender más"
   - **Nivel B** (política de cookies): enlace a política específica de cookies
   - **Nivel C** (política general): enlace a política de privacidad genérica
5. Determina el veredicto.

**Veredicto:**
- `PASSED` — Banner detectado + al menos un elemento Nivel A o B (Capa 2 accesible)
- `WARNING` — Banner detectado pero solo Nivel C (política de privacidad general)
- `FAILED` — No se detecta banner, o banner sin ningún enlace a Capa 2

**Uso:**
```bash
python3 r1_capas.py                                  # directorio PoliGraph por defecto (elpais)
python3 r1_capas.py /ruta/poligraph/output/          # directorio con accessibility_tree.json
python3 r1_capas.py /ruta/accessibility_tree.json    # fichero directo
python3 r1_capas.py /ruta/poligraph/output/ --no-detalle
```

---

### `analysis_scripts/r14_lenguaje.py`

**Requisito que implementa:** R14 — Lenguaje claro y sencillo (RGPD Art. 12 / AEPD)

**Por qué:**
RGPD Art. 12 exige que la información al usuario sea redactada en lenguaje claro y sencillo,
evitando tecnicismos y frases excesivamente largas. Su incumplimiento dificulta el consentimiento
informado del usuario.

**Fuentes de datos:**
- `readability.json` (PoliGraph) — texto limpio de la política de privacidad
- `accessibility_tree.json` (PoliGraph) — texto del banner de cookies (opcional, reutiliza lógica de R1)

**Cómo funciona:**

*Parte 1 — Política de privacidad (índice Szigriszt-Muñoz):*
- Extrae el texto limpio de `readability.json` con BeautifulSoup
- Cuenta oraciones, palabras y sílabas (heurística de grupos vocálicos para español)
- Aplica la fórmula: `206.835 - 62.3 × (sílabas/palabras) - (palabras/oraciones)`
- Clasifica: ≥ 50 → PASSED | 35-49 → WARNING | < 35 → FAILED

*Parte 2 — Banner de cookies (longitud media de frase):*
- Extrae el texto visible del banner (mismo DFS que R1 sobre el árbol de accesibilidad)
- Calcula la media de palabras por frase
- ≤ 25 palabras/frase → PASSED | > 25 → WARNING

*Parte 3 — Tecnicismos legales (informativo):*
- Busca 26 patrones de jerga RGPD/legal habituales en español
- No cambia el veredicto, enriquece el informe con cuáles aparecen y cuántas veces

**Veredicto global:** el peor de (política, banner).

**Bug conocido corregido:** el regex original del croquis `r"[\n\r\t\\n\\t\\r]+"` dentro
de una clase de caracteres `[...]` con raw strings de Python hacía que `\\n`, `\\t`, `\\r`
coincidieran con las letras literales `n`, `t`, `r`, corrompiendo todo el texto. Se corrigió
usando `.replace("\\n", " ")` explícito antes de la normalización de espacios.

**Uso:**
```bash
python3 r14_lenguaje.py                                 # directorio PoliGraph por defecto (elpais)
python3 r14_lenguaje.py /ruta/poligraph/output/         # directorio con readability.json + accessibility_tree.json
python3 r14_lenguaje.py /ruta/readability.json          # solo política
python3 r14_lenguaje.py /ruta/poligraph/output/ --no-detalle
```

---

### `analysis_scripts/r4_granularidad.js`

**Requisito que implementa:** R4 — Granularidad en la elección (RGPD Art. 7 / Considerando 43)

**Por qué:**
RGPD Art. 7 y el Considerando 43 exigen que el consentimiento sea específico por finalidad:
el usuario debe poder aceptar las cookies analíticas sin aceptar las publicitarias. Un banner
que solo ofrece "Aceptar todo" o "Rechazar todo" no cumple este principio.

**Herramienta:** Playwright + `puppeteer-extra-plugin-stealth` (Node.js). Se mantiene Node.js
porque el plugin stealth ya estaba configurado y funciona bien con este CMP.

**Cómo funciona:**
1. Navega al sitio y espera 4s a que aparezca el banner de cookies.
2. Busca en todos los frames (iframes) un botón/enlace de configuración mediante regex
   (`configurar|preferencias|opciones|settings|customize|choices|cookie policy`).
3. Hace clic en él y espera 5s a que cargue el panel.
4. Cuenta los indicadores de granularidad en todos los frames:
   - **Indicador A** (primario): `[role="checkbox"]`, `[role="switch"]`, `input[type="checkbox"]`
   - **Indicador B** (secundario): botones "Aceptar" repetidos por categoría (> 2 → hay uno por categoría)
5. Veredicto: `totalOpciones = max(A, B)`. ≥ 2 → PASSED, < 2 → FAILED.
6. Guarda captura del panel abierto en `analysis_data/r4_panel_{dominio}.png`.

**Mejoras respecto al script original (`prueba_r4.js`):**
- URL como argumento CLI (antes hardcodeada a abc.es)
- Eliminado `contenedoresOpciones`: contaba todos los div/li con palabras clave de finalidad
  en cualquier nivel del DOM, devolviendo valores inflados (47 para Decathlon) que no
  medían granularidad real.
- Salida JSON en `analysis_data/r4_resultado.json` (coherente con el resto de scripts)
- Screenshot guardado en `analysis_data/` en vez del directorio de trabajo

**Extracción de nombres de categoría — CDP:**
`page.accessibility` fue eliminado en Playwright v1.47+. Se usa el Chrome DevTools
Protocol (CDP) directamente via `page.context().newCDPSession(page)` y el comando
`Accessibility.getFullAXTree`, que devuelve el árbol de accesibilidad completo
incluyendo Shadow DOM en un array plano ordenado por DFS. La heurística consiste en
buscar hacia atrás en ese array, desde cada toggle, el heading más cercano que lo
precede (máx. 20 posiciones) — ese heading es el nombre de la categoría.
Esto funciona con Didomi (Decathlon) y es robusto frente a cualquier estructura
interna del CMP porque CDP es agnóstico al Shadow DOM.

**Uso:**
```bash
node r4_granularidad.js                         # URL por defecto (decathlon.es)
node r4_granularidad.js https://sitio.com       # URL explícita
node r4_granularidad.js https://sitio.com --no-detalle
```

---

### `analysis_scripts/r5_revocabilidad.py`

**Requisito que implementa:** R5 — Revocabilidad sencilla (RGPD Art. 7.3 / AEPD)

**Por qué:**
RGPD Art. 7.3 exige literalmente que "será tan fácil retirar el consentimiento como darlo".
Si el banner muestra "Aceptar todo" en primera capa pero rechazar requiere pasar por un
panel de configuración, existe una asimetría que vulnera este principio.

**Fuente de datos:** `accessibility_tree.json` generado por PoliGraph.

**Cómo funciona:**
1. Localiza el banner de cookies (mismo DFS que R1 y R14).
2. Extrae todos los botones y enlaces del banner y los clasifica en:
   - **Rechazo directo**: "Rechazar todo", "Decline", "Disagree and close", "Continuar sin aceptar"…
   - **Aceptación**: "Aceptar todo", "Accept all", "Agree and close"…
   - **Configuración**: "Configurar", "Preferencias", "Settings", "Learn More"…
3. Aplica el test de simetría:
   - Rechazo directo en banner → **PASSED** (simétrico con "Aceptar")
   - Solo configuración en banner → **WARNING** (un click extra para rechazar)
   - Ninguna opción → **FAILED**

**Diferencia con el script original (`r5.py`):**
El script anterior medía la "profundidad en el árbol JSON" como proxy de clicks.
Esta métrica es incorrecta: un botón visible en el banner puede estar en el nivel 15
del JSON. Se sustituyó por el test de simetría, que mide directamente si rechazar
es equivalente en facilidad a aceptar, que es lo que exige el RGPD.

**Uso:**
```bash
python3 r5_revocabilidad.py                                # directorio por defecto (elpais)
python3 r5_revocabilidad.py /ruta/poligraph/output/        # directorio con accessibility_tree.json
python3 r5_revocabilidad.py /ruta/accessibility_tree.json  # fichero directo
python3 r5_revocabilidad.py /ruta/poligraph/output/ --no-detalle
```

---

### `analysis_scripts/r16_correspondencia.py`

**Requisito que implementa:** R16 — Correspondencia entre el aviso y la ejecución (RGPD Art. 13/14 / Art. 5.1.a)

**Por qué:**
RGPD Art. 13/14 exige que el responsable informe al interesado sobre todos los destinatarios
o categorías de destinatarios de los datos personales. Si el sitio envía datos a terceros
que no están declarados en la política, hay una infracción directa del principio de
transparencia (Art. 5.1.a).

**Fuentes de datos:**
- `graph-original.full.yml` (PoliGraph) — nodos ACTOR = entidades declaradas en la política
- `3p_domains.csv` (webXray) — dominios de terceros detectados, con propietario y linaje corporativo
- DuckDuckGo Tracker Radar (`tracker-radar/domains/`) — resolución de propietario para dominios
  que webXray no identifica

**Cómo funciona:**

*Carga de actores declarados:*
Extrae todos los nodos de tipo `ACTOR` del grafo YAML de PoliGraph (excluyendo
`UNSPECIFIED_ACTOR` y `we`). Estos son los nombres de empresa o entidad mencionados
explícitamente en el texto de la política de privacidad.

*Resolución del propietario de cada dominio:*
1. webXray `3p_domains.csv` ya proporciona `owner` y `owner_lineage` para dominios conocidos.
   El linaje es la cadena de empresas matrices: `"Adobe Audience Manager > Adobe Experience Cloud > Adobe Systems"`.
2. Para dominios sin propietario en webXray, se consulta DuckDuckGo Tracker Radar
   (22.000+ dominios clasificados). DDG da `owner.name` y `owner.displayName`.
3. Si ninguna fuente tiene el propietario, el dominio se clasifica como "sin owner identificable"
   (podría ser infraestructura/CDN, no un procesador de datos).

*Coincidencia de tokens (matching fuzzy):*
Se normalizan todos los nombres (minúsculas, sin puntuación) y se extraen tokens significativos
(longitud ≥ 4 chars, sin stopwords como "inc", "llc", "corp", "media", "digital"…).
Un tercero está "declarado" si algún token de su nombre (o de cualquier entrada de su linaje)
aparece en los tokens de algún actor de la política.

Ejemplos: `"Adobe Systems"` → tokens `{"adobe", "systems"}`. Política dice `"Adobe Experience Cloud"` → tokens `{"adobe", "experience", "cloud"}`. Intersección: `{"adobe"}` → declarado.

*Clasificación de discrepancias:*
- **No declarado identificado** (→ FAILED): el dominio tiene propietario conocido (en webXray o DDG) que no aparece en la política.
- **Sin propietario identificable** (→ WARNING): ninguna fuente conoce el dominio; podría ser CDN o infraestructura propia.
- **Declarado** (→ PASSED): propietario con coincidencia en la política.

**Veredicto:**
- `PASSED` — Todos los dominios de terceros tienen propietario declarado en la política
- `WARNING` — Solo dominios sin propietario identificable quedan sin declarar (no se puede confirmar que sean data processors)
- `FAILED` — Al menos un dominio tiene propietario identificado que NO está en la política

**Limitación conocida — PoliGraph y políticas en español:**
PoliGraph está optimizado para políticas en inglés. Para sitios españoles, puede extraer
entidades genéricas en inglés en lugar de nombres de empresa precisos (ej: "payment gateway"
en lugar de "PayPal"), lo que dificulta el matching. Esto debe mencionarse como limitación
del entorno de pruebas en el documento final.

**Uso:**
```bash
python3 r16_correspondencia.py                                        # rutas por defecto
python3 r16_correspondencia.py /ruta/poligraph/ /ruta/webxray/sitio/  # ambos directorios
python3 r16_correspondencia.py /ruta/graph.full.yml /ruta/3p_domains.csv  # ficheros directos
python3 r16_correspondencia.py /ruta/poligraph/ /ruta/webxray/ --no-detalle
```

---

### `analysis_scripts/r2_r3_cookies_beacons.py`

**Requisito que implementa:** R2 — Bloqueo de cookies no exentas (RGPD Art. 5.1.a, 6 y 7 / LSSI Art. 22.2 / Directiva ePrivacy)

**Por qué:**
La Directiva ePrivacy (transpuesta en LSSI Art. 22.2) exige que las cookies que no son
estrictamente necesarias solo se instalen tras el consentimiento expreso del usuario.
Su presencia antes de la interacción con el banner es una infracción directa.

**Fuente de datos:** MySQL tabla `entries` de Privacy Pioneer.
Campo `consent_phase = 'PRE'` para eventos anteriores al consentimiento.

**Tipos evaluados:**
| Tipo PP | Descripción | Requiere consentimiento |
|---|---|---|
| `advertising` | Publicidad comportamental | Sí → FAILED si en PRE |
| `analytics` | Analítica de comportamiento | Sí → FAILED si en PRE |
| `social` | Redes sociales | Sí → FAILED si en PRE |
| `trackingPixel` | Píxel de seguimiento | Sí → FAILED si en PRE |
| `fingerprinting` | Huella digital | Sí → FAILED si en PRE |
| `ipAddress`, `region`, `city` | Información de red | Exentos — no se evalúan |

**Cómo funciona:**
1. Consulta MySQL filtrando `consent_phase = 'PRE'` y los tipos no exentos.
2. Deduplica por `(dominio_rastreador, tipo)` — el mismo tracker no aparece N veces aunque se cargue desde varias páginas del sitio.
3. Agrupa las violaciones por tipo para la salida detallada.
4. Evalúa por sitio: FAILED si hay cualquier rastreador no exento en PRE, PASSED si ninguno.

**Veredicto:**
- `PASSED` — Ningún rastreador no exento activo antes del consentimiento
- `FAILED` — Al menos un rastreador no exento detectado en fase PRE

**Uso:**
```bash
python3 r2_r3_cookies_beacons.py              # todos los sitios en la BD
python3 r2_r3_cookies_beacons.py abc.es       # filtrar por sitio (substring)
python3 r2_r3_cookies_beacons.py --no-detalle # solo resumen
```
