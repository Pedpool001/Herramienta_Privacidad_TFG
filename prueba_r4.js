const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();
chromium.use(stealth);

async function auditoriaHeuristicaR4(url) {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();

    console.log(`[*] Iniciando Auditoría Universal R4 en: ${url}`);

    try {
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
        await page.waitForTimeout(4000); 

        // 1. LOCALIZACIÓN SEMÁNTICA DEL ACCESO
        // Buscamos cualquier cosa que PAREZCA un acceso a configuración
        const regexConfig = /configurar|preferencias|opciones|personalizar|manage|settings|customize|choices|cookie policy/i;
        let btnAcceso = null;

        for (const frame of page.frames()) {
            const candidato = frame.locator('button, a, [role="button"], [role="link"]')
                                   .filter({ hasText: regexConfig }).first();
            if (await candidato.isVisible()) {
                btnAcceso = candidato;
                break;
            }
        }

        if (btnAcceso) {
            console.log("[!] Acceso al panel localizado. Entrando...");
            await btnAcceso.click({ force: true });
            await page.waitForTimeout(5000); // Tiempo para que cargue cualquier panel dinámico

            // 2. CAPTURA DE EVIDENCIA
            await page.screenshot({ path: 'evidencia_granularidad.png' });

            // 3. CONTEO HEURÍSTICO DE OPCIONES (El "corazón" del script)
            // En lugar de clases, buscamos "Patrones de Elección"
            let totalOpciones = 0;

            for (const frame of page.frames()) {
                // Patrón A: Elementos con roles de entrada de datos (Checkboxes/Switches)
                const rolesEleccion = await frame.locator('[role="checkbox"], [role="switch"], input[type="checkbox"], [role="radio"]').count();
                
                // Patrón B: Grupos de botones repetitivos (Aceptar/Rechazar por categoría)
                // Si hay muchos botones con el mismo texto dentro de un panel, son opciones granulares
                const botonesAceptar = await frame.locator('button:has-text("Aceptar"), button:has-text("Accept"), button:has-text("Si")').count();
                
                // Patrón C: Contenedores de opciones (Heurística de diseño)
                // Buscamos elementos que se repiten y contienen palabras clave legales
                const contenedoresOpciones = await frame.locator('div, li').filter({ 
                    hasText: /publicidad|marketing|análisis|analítica|estadísticas|funcionales|personalización/i 
                }).count();

                // Sumamos la lógica: priorizamos roles, luego contenedores
                totalOpciones += Math.max(rolesEleccion, (botonesAceptar > 2 ? botonesAceptar - 1 : 0), contenedoresOpciones);
            }

            console.log("\n--- RESULTADO DE LA AUDITORÍA SEMÁNTICA ---");
            // Un panel sin granularidad suele tener 0 o 1 opción (la de "necesarias")
            // Un panel granular siempre tendrá al menos 2 o más categorías
            if (totalOpciones >= 2) {
                console.log(`[✅ PASS] R4 CUMPLIDO: Se han detectado ${totalOpciones} puntos de decisión independientes.`);
                console.log(`[*] El sistema permite una selección específica de finalidades.`);
            } else {
                console.log(`[❌ FAIL] R4 INCUMPLIDO: Solo se han detectado ${totalOpciones} opciones.`);
                console.log(`[*] No se observa capacidad de elección granular para el usuario.`);
            }

        } else {
            console.log("[❌ FAIL] No se pudo localizar el punto de configuración de privacidad.");
        }

    } catch (error) {
        console.error(`[-] Error técnico: ${error.message}`);
    } finally {
        await browser.close();
        console.log("[*] Auditoría finalizada. Evidencia guardada en 'evidencia_granularidad.png'.");
    }
}

// Prueba con cualquier web
auditoriaHeuristicaR4('https://www.abc.es');
