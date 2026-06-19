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
const ANALYSIS_DATA = path.resolve(
    __dirname,
    '../analysis_data'
);
const RESULTADO_PATH = path.join(ANALYSIS_DATA, 'r4_resultado.json');

// ── Configuración ─────────────────────────────────────────────────────────────
const URL_DEFAULT  = 'https://www.decathlon.es';
const TIMEOUT_NAV  = 60000;
const TIMEOUT_PANEL = 5000;

// Regex para localizar el botón de acceso al panel de configuración
const REGEX_CONFIG = /configurar|preferencias|opciones|personalizar|manage|settings|customize|choices|cookie\s*(?:settings|policy|preferences)/i;

// Regex para el botón global de aceptar (solo para el conteo de tipo B)
const REGEX_ACEPTAR = /^(aceptar|accept|si|yes|accepter|akzeptieren)$/i;


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
        await page.waitForTimeout(4000);

        // ── 1. Localizar botón de acceso al panel ─────────────────────────────
        let btnAcceso = null;
        let frameAcceso = null;

        for (const frame of page.frames()) {
            const candidato = frame
                .locator('button, a, [role="button"], [role="link"]')
                .filter({ hasText: REGEX_CONFIG })
                .first();
            if (await candidato.isVisible().catch(() => false)) {
                btnAcceso   = candidato;
                frameAcceso = frame;
                break;
            }
        }

        if (!btnAcceso) {
            resultado.evaluacion.detalle =
                'No se localizó ningún botón de configuración de cookies en el banner. ' +
                'El sitio puede no mostrar el banner en esta visita (cookies previas, ' +
                'geobloqueo) o usar un acceso de configuración con texto no reconocido.';
            return resultado;
        }

        const textoBtn = (await btnAcceso.innerText().catch(() => '')).trim();
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
