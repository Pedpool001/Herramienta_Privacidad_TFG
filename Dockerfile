FROM ubuntu:22.04

# ── Variables de entorno básicas ──────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Madrid \
    PATH="/opt/conda/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    FIREFOX_BINARY_PATH=/opt/firefox-nightly/firefox

# ── Paquetes del sistema ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Utilidades base
    curl wget git ca-certificates gnupg unzip \
    build-essential python3-dev \
    # Cliente MySQL (para health-check y depuración)
    default-mysql-client \
    # Dependencias de Chromium/Playwright
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libgbm1 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libxss1 libxtst6 libasound2 \
    libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    libx11-6 libx11-xcb1 libxcb1 libxcb-dri3-0 \
    libxshmfence1 libxext6 libxfixes3 libxi6 libxrender1 \
    fonts-liberation fonts-noto-color-emoji \
    # Dependencias de Firefox (Privacy Pioneer + PoliGraph)
    libgtk-3-0 libdbus-glib-1-2 \
    # xz-utils para descomprimir Firefox Nightly (.tar.xz)
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Firefox Nightly (Privacy Pioneer) ────────────────────────────────────────
RUN curl -fsSL \
    "https://download.mozilla.org/?product=firefox-nightly-latest-ssl&os=linux64&lang=en-US" \
    -o /tmp/firefox-nightly.tar \
    && tar -xf /tmp/firefox-nightly.tar -C /opt/ \
    && mv /opt/firefox /opt/firefox-nightly \
    && rm /tmp/firefox-nightly.tar

# ── Node.js 20 LTS ────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Miniconda ─────────────────────────────────────────────────────────────────
RUN wget -q https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh \
        -O /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm /tmp/miniforge.sh \
    && conda clean -afy

# ── Directorio de trabajo ─────────────────────────────────────────────────────
WORKDIR /app

# ── Copiar código fuente ──────────────────────────────────────────────────────
# .dockerignore excluye node_modules, __pycache__, venv, output, etc.
COPY . .

# ── Herramientas de sistema adicionales ───────────────────────────────────────
# psmisc: fuser (liberar puerto 8080 antes de la REST API)
# xvfb:   display virtual para Firefox headful (Privacy Pioneer en Docker)
RUN apt-get update && apt-get install -y --no-install-recommends \
    psmisc \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# ── Entorno conda: openwpm ────────────────────────────────────────────────────
RUN for i in 1 2 3; do \
        conda env create -f openWPM/OpenWPM/environment.yaml && break; \
        echo "Reintento $i en 30s..."; sleep 30; \
    done \
    && conda clean -afy

# ── Entorno conda: poligraph ──────────────────────────────────────────────────
# El environment.yml tiene name: nlp20230531; lo creamos como "poligraph"
RUN for i in 1 2 3; do \
        conda env create -f PoliGraph/environment.yml -n poligraph && break; \
        echo "Reintento $i en 30s..."; sleep 30; \
    done \
    && conda run -n poligraph pip install --no-cache-dir \
        -e /app/PoliGraph/ \
        deep-translator \
    && conda run -n poligraph playwright install firefox chromium \
    && conda clean -afy

# ── Entorno virtual webXray ───────────────────────────────────────────────────
RUN python3 -m venv /app/webXray/venv_tfg \
    && /app/webXray/venv_tfg/bin/pip install --no-cache-dir --upgrade pip \
    && sed -e 's/^lxml.*/lxml>=5.0/' \
           -e 's/^psycopg2-binary.*/psycopg2-binary>=2.9/' \
       /app/webXray/requirements.txt > /tmp/wx_req.txt \
    && /app/webXray/venv_tfg/bin/pip install --no-cache-dir -r /tmp/wx_req.txt \
    && rm /tmp/wx_req.txt

# ── Dependencias Node.js ──────────────────────────────────────────────────────
RUN cd /app/WEC/website-evidence-collector \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

RUN cd /app/BL/blacklight-collector \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

RUN cd /app/privacy-pioneer-web-crawler/selenium-crawler \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

RUN cd /app/Herramienta_Priv/requisitos \
    && npm install --no-audit --no-fund \
    && npx playwright install chromium

RUN cd /app/privacy-pioneer-web-crawler/rest-api \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

# ── Paquetes Python para la aplicación principal ──────────────────────────────
RUN pip3 install --no-cache-dir \
    flask \
    "playwright>=1.47" \
    playwright-stealth \
    ddgs \
    pdfplumber \
    mysql-connector-python \
    beautifulsoup4 \
    lxml \
    requests \
    requests-cache \
    langdetect \
    pyyaml \
    tldextract

# ── Navegadores Playwright (Chromium para la app principal) ───────────────────
RUN playwright install chromium \
    && playwright install-deps chromium

# ── Puerto expuesto ───────────────────────────────────────────────────────────
EXPOSE 5000

# ── Punto de entrada ──────────────────────────────────────────────────────────
CMD ["python3", "Herramienta_Priv/api.py", "--host", "0.0.0.0", "--port", "5000"]
