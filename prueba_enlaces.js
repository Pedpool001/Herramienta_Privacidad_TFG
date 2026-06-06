const { chromium } = require('playwright');

async function mapearRecursos(urlObjetivo) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();

  console.log(`[*] Iniciando mapeo total de: ${urlObjetivo}`);

  try {
    // Navegamos a la web
    await page.goto(urlObjetivo, { waitUntil: 'domcontentloaded', timeout: 30000 });
    
    // Esperamos un poco para que cargue el contenido dinámico
    await page.waitForTimeout(2000);

    // 1. EXTRAEMOS ABSOLUTAMENTE TODOS LOS ENLACES (Tu idea del Ctrl+F)
    const todosLosEnlaces = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      return anchors.map(a => ({
        href: a.href,
        texto: (a.innerText || "").toLowerCase().trim()
      }));
    });

    console.log(`[!] Se han encontrado ${todosLosEnlaces.length} recursos accesibles.`);

    // 2. APLICAMOS EL FILTRO (Tu lógica de búsqueda)
    const palabrasClave = ['privacidad', 'privacy', 'legal', 'cookies', 'aviso'];
    
    const coincidencias = todosLosEnlaces.filter(item => {
      const url = item.href.toLowerCase();
      // Buscamos coincidencia en la URL o en el texto del botón/enlace
      return palabrasClave.some(p => url.includes(p) || item.texto.includes(p));
    });

    // Eliminamos duplicados para limpiar la salida
    const unicos = [...new Map(coincidencias.map(v => [v.href, v])).values()];

    if (unicos.length > 0) {
      console.log("\n[+] RECURSOS LEGALES DETECTADOS:");
      unicos.forEach(c => {
        console.log(`  > Texto: "${c.texto.padEnd(20)}" | URL: ${c.href}`);
      });
      
      // Intentamos predecir cuál es la principal
      const principal = unicos.find(u => u.href.includes('politica') || u.href.includes('privacy')) || unicos[0];
      console.log(`\n[*] Candidato principal para PoliGraph: ${principal.href}`);
    } else {
      console.log("[-] No se encontraron recursos que coincidan con la búsqueda.");
    }

  } catch (err) {
    console.error(`[-] Error al mapear: ${err.message}`);
  } finally {
    await browser.close();
  }
}

mapearRecursos('https://www.abc.es');
