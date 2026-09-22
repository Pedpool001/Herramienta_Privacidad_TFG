/**
 * R4 - Granularidad en la elección
 * (RGPD Art. 7 / Considerando 43 / AEPD)
 *
 * Verifica que el usuario puede aceptar o denegar el uso de cookies de forma
 * específica por finalidad (analítica, publicitaria, funcional…) desde el
 * banner o panel de configuración del sitio.
 *
 * Herramienta: Playwright + stealth (Node.js)
 *
 * Lógica:
 *   1. Navega al sitio y espera a que aparezca el banner de cookies.
 *   2. Busca un botón/enlace de configuración ("Configurar", "Preferencias"…).
 *   3. Si lo encuentra, hace clic y espera a que cargue el panel.
 *   4. Dentro del panel cuenta los indicadores de granularidad:
 *      - Indicador A: toggles independientes (checkbox / switch / radio)
 *      - Indicador B: botones Aceptar/Rechazar repetidos por categoría
 *        (más de 2 botones "Aceptar" → hay uno por categoría además del global)
 *   5. PASS si indicador ≥ 2, FAIL si no.
 *   6. Intenta extraer los nombres de las categorías vía aria-label / title.
 *
 * Nota sobre contenedoresOpciones: el script original contaba todos los div/li
 * que contenían palabras clave, lo que daba recuentos inflados (ej: 47 en
 * Decathlon). Se eliminó porque ese número no mide granularidad real — solo
 * confirma que el panel habla de cookies, no que el usuario pueda elegir.
 *
 * Uso:
 *   node r4_granularidad.js                    # URL por defecto (decathlon.es)
 *   node r4_granularidad.js https://sitio.com  # URL explícita
 *   node r4_granularidad.js https://sitio.com --no-detalle
 */

const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();
const path = require('path');
const fs = require('fs');

chromium.use(stealth);

// ── Rutas de salida ───────────────────────────────────────────────────────────
// ANALYSIS_DATA_DIR permite aislar la salida por sitio cuando la auditoría
// corre en paralelo; sin la variable se usa el directorio compartido de siempre.
const ANALYSIS_DATA = process.env.ANALYSIS_DATA_DIR
    ? path.resolve(process.env.ANALYSIS_DATA_DIR)
    : path.resolve(__dirname, '../analysis_data');
const RESULTADO_PATH = path.join(ANALYSIS_DATA, 'r4_resultado.json');

// ── Configuración ─────────────────────────────────────────────────────────────
const URL_DEFAULT  = 'https://www.decathlon.es';
const TIMEOUT_NAV  = 60000;
const TIMEOUT_PANEL = 5000;

// Regex para localizar el botón de acceso al panel de configuración.
// Cubre el vocabulario de los CMP más extendidos en España (Didomi, OneTrust,
// Cookiebot) y el estándar IAB TCF. Notas:
//   - Se usa el stem "configura" para casar tanto "configurar" como
//     "configuración" (la regex anterior solo tenía "configurar" y fallaba con
//     "Configuración de cookies").
//   - "personaliza" cubre personalizar/personalización.
//   - "gestionar", "ajustes", "más opciones/información" son variantes comunes
//     de acceso al panel en español.
//   - "purposes"/"manage"/"more options" corresponden al panel del IAB TCF.
const REGEX_CONFIG = new RegExp(
    [
        'configura', 'configuraci[oó]n',
        'preferencias', 'opciones', 'personaliza',
        'gestionar', 'administrar', 'ajustes',
        'm[aá]s\\s+(?:opciones|informaci[oó]n|detalles)',
        'ver\\s+(?:opciones|preferencias|detalles)',
        'manage', 'settings', 'customize', 'choices', 'options',
        'purposes', 'more\\s+options', 'show\\s+purposes',
        // "policy" queda fuera a propósito: "Cookie Policy" / "Política de
        // cookies" es un enlace al documento, no un acceso a la configuración,
        // y casarlo hacía clic en la política en vez de en el panel de toggles.
        'cookie\\s*(?:settings|preferences)',
    ].join('|'),
    'i',
);

// Regex para el botón de aceptar por categoría (conteo de tipo B). Anclada con
// ^…$ a propósito: cuenta botones "Aceptar" individuales por categoría, no el
// "Aceptar todo" global. Se añaden variantes de activación por categoría que
// usan algunos CMP (permitir/allow/activar/on) además de los "sí/aceptar".
const REGEX_ACEPTAR = /^(aceptar|acepto|accept|permitir|allow|activar|s[ií]|yes|on|accepter|akzeptieren|zulassen)$/i;


// Palabras de acción que NO son nombres de categoría
const REGEX_ACCION = /^(aceptar|rechazar|accept|reject|save|guardar|ok|cancel|cancelar|si|no|yes|always\s+active|siempre\s+activo)$/i;

// ── Extracción de categorías via Chrome DevTools Protocol ────────────────────
//
// page.accessibility fue eliminado en Playwright v1.47+. Usamos CDP directamente,
// que es la API subyacente que Playwright usaba internamente.
//
// CDP devuelve el árbol de accesibilidad completo en un array plano ordenado
// como un recorrido en profundidad (DFS). Esto significa que los nodos que son
// vecinos en el árbol DOM aparecen también como vecinos en el array.
//
// Heurística para extraer nombres de categoría:
//   Para cada toggle (radio/checkbox/switch), buscamos hacia atrás en el array
//   el heading más cercano que lo precede — ese heading es el título de la
//   categoría a la que pertenece el toggle. Limitamos la búsqueda a 20
//   posiciones para no capturar headings de otras secciones de la página.
//
// Esto funciona con Didomi (y otros CMPs que usan Shadow DOM) porque CDP expone
// el árbol completo del navegador independientemente de las fronteras de Shadow.
async function extraerCategorias(page) {
    try {
        const client = await page.context().newCDPSession(page);
        const { nodes } = await client.send('Accessibility.getFullAXTree');
        await client.detach();

        const ROLES_TOGGLE = new Set(['checkbox', 'switch', 'radio']);
        const categorias   = [];

        nodes.forEach((nodo, idx) => {
            if (!ROLES_TOGGLE.has(nodo.role?.value)) return;
            // Buscar hacia atrás el heading más cercano (máx. 20 posiciones)
            for (let i = idx - 1; i >= Math.max(0, idx - 20); i--) {
                const nombre = nodes[i].name?.value?.trim();
                if (nodes[i].role?.value === 'heading' && nombre && !REGEX_ACCION.test(nombre)) {
                    if (!categorias.includes(nombre)) categorias.push(nombre.slice(0, 60));
                    break;
                }
            }
        });

        return categorias;
    } catch (e) {
        return [];
    }
}


// Selectores de contenedor de banner de los CMP más extendidos. Acotar la
// búsqueda del botón a estos contenedores evita casar con texto ajeno al banner
// (p. ej. titulares de noticias que contengan "opciones"), que era un falso
// positivo real en webs de noticias.
const SELECTOR_BANNER = [
    '[id*="didomi" i]', '[class*="didomi" i]',
    '#onetrust-banner-sdk', '[id*="onetrust" i]', '[class*="onetrust" i]', '[class*="ot-sdk" i]',
    '[id*="cookiebot" i]', '#CybotCookiebotDialog',
    '[id*="usercentrics" i]', '[class*="uc-banner" i]',
    '[id*="cmp" i]', '[class*="cmp" i]',
    '[id*="consent" i]', '[class*="consent" i]',
    '[id*="cookie" i]', '[class*="cookie" i]',
    '[role="dialog"]', '[role="alertdialog"]',
    '[aria-label*="cookie" i]', '[aria-label*="consent" i]', '[aria-label*="privacidad" i]',
].join(',');

// Regex estricta para el fallback a página completa (sin banner identificable):
// solo términos inequívocamente de configuración de cookies, para no casar con
// contenido genérico de la página.
const REGEX_CONFIG_ESTRICTO = /configura|preferencias|personaliza|gestionar\s+cookies|ajustes\s+de\s+cookies|cookie\s*(?:settings|preferences)|manage\s+(?:cookies|preferences|options)|show\s+purposes/i;


// Busca dentro de `locator` el primer botón/enlace visible cuyo texto, aria-label
// o title case con `regex`. Comprobar aria-label/title (no solo el texto visible)
// capta los botones de icono (engranaje ⚙) o con nombre accesible por atributo.
// Recoge TODOS los índices que casan y devuelve el primero visible: el primer
// candidato puede estar oculto, y descartar por él perdería un botón visible
// posterior (p. ej. "Personaliza tus Cookies" tras enlaces de política).
async function _primerBotonVisible(locator, regex) {
    const indices = await locator.evaluateAll((els, reSrc) => {
        const re  = new RegExp(reSrc, 'i');
        const out = [];
        for (let i = 0; i < els.length; i++) {
            const el    = els[i];
            const texto = (el.innerText || el.textContent || '').trim();
            const aria  = el.getAttribute('aria-label') || '';
            const title = el.getAttribute('title') || '';
            if (re.test(`${texto} ${aria} ${title}`)) out.push(i);
        }
        return out;
    }, regex.source).catch(() => []);

    for (const idx of indices) {
        const el = locator.nth(idx);
        if (await el.isVisible().catch(() => false)) {
            const texto = ((await el.innerText().catch(() => '')) ||
                           (await el.getAttribute('aria-label').catch(() => '')) ||
                           (await el.getAttribute('title').catch(() => '')) || '').trim();
            return { btn: el, texto };
        }
    }
    return null;
}


// Localiza el botón de acceso al panel de configuración.
//   1º) Dentro de los contenedores de banner conocidos, con la regex generosa.
//   2º) Si no hay banner identificable, en toda la página pero con regex estricta.
async function localizarBotonConfig(page) {
    // Paso 1: acotado al banner (preciso, evita falsos positivos de contenido)
    for (const frame of page.frames()) {
        const dentroBanner = frame.locator(SELECTOR_BANNER).locator(
            'button, a, [role="button"], [role="link"], [role="menuitem"]'
        );
        const res = await _primerBotonVisible(dentroBanner, REGEX_CONFIG);
        if (res) return { ...res, frame };
    }
    // Paso 2: fallback a página completa con criterio estricto
    for (const frame of page.frames()) {
        const todos = frame.locator(
            'button, a, [role="button"], [role="link"], [role="menuitem"]'
        );
        const res = await _primerBotonVisible(todos, REGEX_CONFIG_ESTRICTO);
        if (res) return { ...res, frame };
    }
    return null;
}


// ── Auditoría principal ───────────────────────────────────────────────────────
async function auditarR4(url, mostrarDetalle) {
    const browser = await chromium.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
    const page    = await browser.newPage();

    const resultado = {
        sitio:     new URL(url).hostname,
        url,
        veredicto: 'FAILED',
        evaluacion: { estado: 'FAILED', detalle: '' },
        hallazgos: {
            panel_localizado:      false,
            boton_config_texto:    null,
            indicador_toggles:     0,
            indicador_botones:     0,
            total_opciones:        0,
            categorias_detectadas: [],
            screenshot:            null,
        },
    };

    try {
        console.log(`[*] Navegando a: ${url}`);
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: TIMEOUT_NAV });
        await page.waitForTimeout(5000);   // margen para CMP que inyectan el banner tarde

        // ── 1. Localizar botón de acceso al panel ─────────────────────────────
        const acceso    = await localizarBotonConfig(page);
        const btnAcceso  = acceso ? acceso.btn : null;

        if (!btnAcceso) {
            resultado.evaluacion.detalle =
                'No se localizó ningún botón de configuración de cookies en el banner. ' +
                'El sitio puede no mostrar el banner en esta visita (cookies previas, ' +
                'geobloqueo) o usar un acceso de configuración con texto no reconocido.';
            return resultado;
        }

        // Texto del botón: el que devolvió el localizador (puede venir del
        // aria-label/title si no había texto visible, p. ej. botones de icono).
        const textoBtn = acceso.texto || (await btnAcceso.innerText().catch(() => '')).trim();
        resultado.hallazgos.boton_config_texto = textoBtn;
        console.log(`[!] Botón de configuración encontrado: "${textoBtn}". Abriendo panel...`);

        await btnAcceso.click({ force: true });
        await page.waitForTimeout(TIMEOUT_PANEL);

        resultado.hallazgos.panel_localizado = true;

        // ── 2. Tomar captura del panel ────────────────────────────────────────
        fs.mkdirSync(ANALYSIS_DATA, { recursive: true });
        const screenshotName = `r4_panel_${resultado.sitio.replace(/\./g, '_')}.png`;
        const screenshotPath = path.join(ANALYSIS_DATA, screenshotName);
        await page.screenshot({ path: screenshotPath, fullPage: false });
        resultado.hallazgos.screenshot = screenshotPath;
        console.log(`[*] Captura guardada: ${screenshotName}`);

        // ── 3. Contar indicadores de granularidad ─────────────────────────────
        let indicadorToggles  = 0;
        let indicadorBotones  = 0;
        let categorias        = [];

        for (const frame of page.frames()) {
            // Indicador A: toggles independientes (checkbox / switch / radio)
            const toggleLocator = frame.locator(
                '[role="checkbox"], [role="switch"], input[type="checkbox"], [role="radio"]'
            );
            const countToggles = await toggleLocator.count();

            if (countToggles > indicadorToggles) {
                indicadorToggles = countToggles;
            }

            // Indicador B: botones Aceptar/Rechazar repetidos por categoría
            // (más de 2 botones "Aceptar" implica uno por categoría + uno global)
            const countBotones = await frame
                .locator('button')
                .filter({ hasText: REGEX_ACEPTAR })
                .count();
            if (countBotones > indicadorBotones) {
                indicadorBotones = countBotones > 2 ? countBotones - 1 : 0;
            }
        }

        const totalOpciones = Math.max(indicadorToggles, indicadorBotones);

        // Extraer nombres de categoría desde el árbol de accesibilidad del navegador.
        // Este enfoque funciona con cualquier CMP (Didomi, OneTrust, Cookiebot…)
        // porque el navegador resuelve los nombres accesibles de todos los toggles,
        // incluyendo los que están dentro de Shadow DOM.
        categorias = await extraerCategorias(page);

        resultado.hallazgos.indicador_toggles     = indicadorToggles;
        resultado.hallazgos.indicador_botones     = indicadorBotones;
        resultado.hallazgos.total_opciones        = totalOpciones;
        resultado.hallazgos.categorias_detectadas = categorias;

        // ── 4. Veredicto ──────────────────────────────────────────────────────
        if (totalOpciones >= 2) {
            resultado.veredicto                = 'PASSED';
            resultado.evaluacion.estado        = 'PASSED';
            resultado.evaluacion.detalle       =
                `Panel de configuración localizado ("${textoBtn}"). ` +
                `Se detectaron ${totalOpciones} opciones granulares independientes. ` +
                `El usuario puede elegir por finalidad. Cumple RGPD Art. 7.`;
        } else {
            resultado.veredicto          = 'FAILED';
            resultado.evaluacion.estado  = 'FAILED';
            resultado.evaluacion.detalle =
                `Panel localizado ("${textoBtn}") pero solo ${totalOpciones} ` +
                `opción de elección detectada. No se puede seleccionar por finalidad. ` +
                `Incumple RGPD Art. 7 (consentimiento específico por finalidad).`;
        }

    } catch (err) {
        resultado.evaluacion.detalle = `Error técnico: ${err.message}`;
        console.error(`[-] Error: ${err.message}`);
    } finally {
        await browser.close();
    }

    return resultado;
}


// ── Salida por consola ────────────────────────────────────────────────────────
function imprimir(resultado, detalle) {
    const ICONO = { PASSED: '✅', WARNING: '⚠️ ', FAILED: '❌' };
    const v = resultado.veredicto;
    const h = resultado.hallazgos;

    console.log('\n' + '='.repeat(64));
    console.log('  R4 — Granularidad en la elección');
    console.log(`  Sitio    : ${resultado.sitio}`);
    console.log(`  Veredicto: ${ICONO[v] || '?'} ${v}`);
    console.log('='.repeat(64));
    console.log(`\n${resultado.evaluacion.detalle}`);

    if (!detalle) { console.log(); return; }

    console.log('\n' + '─'.repeat(64));
    console.log('Hallazgos:\n');
    console.log(`  Panel localizado        : ${h.panel_localizado ? 'Sí' : 'No'}`);
    if (h.boton_config_texto)
        console.log(`  Botón de acceso         : "${h.boton_config_texto}"`);
    console.log(`  Toggles (checkbox/switch): ${h.indicador_toggles}`);
    console.log(`  Botones por categoría    : ${h.indicador_botones}`);
    console.log(`  Total opciones granulares: ${h.total_opciones}`);

    if (h.categorias_detectadas.length > 0) {
        console.log('\n  Categorías detectadas:');
        h.categorias_detectadas.forEach((c, i) => {
            console.log(`    [${i + 1}] ${c}`);
        });
    }

    if (h.screenshot)
        console.log(`\n  Captura del panel: ${h.screenshot}`);

    console.log();
}


// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
    const args      = process.argv.slice(2).filter(a => a !== '--no-detalle');
    const detalle   = !process.argv.includes('--no-detalle');
    const url       = args[0] || URL_DEFAULT;

    const resultado = await auditarR4(url, detalle);

    fs.mkdirSync(ANALYSIS_DATA, { recursive: true });
    fs.writeFileSync(RESULTADO_PATH, JSON.stringify(resultado, null, 2), 'utf8');
    console.log(`[✓] Resultado guardado en ${RESULTADO_PATH}`);

    imprimir(resultado, detalle);
})();
