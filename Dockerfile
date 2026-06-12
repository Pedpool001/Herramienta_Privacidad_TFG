FROM ubuntu:22.04

# ── Variables de entorno básicas ──────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Madrid \
    PATH="/opt/conda/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

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
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 LTS ────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Miniconda ─────────────────────────────────────────────────────────────────
RUN wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
        -O /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p /opt/conda \
    && rm /tmp/miniconda.sh \
    && conda clean -afy

# ── Directorio de trabajo ─────────────────────────────────────────────────────
WORKDIR /app

# ── Copiar código fuente ──────────────────────────────────────────────────────
# .dockerignore excluye node_modules, __pycache__, venv, output, etc.
COPY . .

# ── Entorno conda: openwpm ────────────────────────────────────────────────────
RUN conda env create -f openWPM/OpenWPM/environment.yaml \
    && conda clean -afy

# ── Entorno conda: poligraph ──────────────────────────────────────────────────
# El environment.yml tiene name: nlp20230531; lo creamos como "poligraph"
RUN conda env create -f PoliGraph/environment.yml -n poligraph \
    && conda run -n poligraph pip install --no-cache-dir \
        -e /app/PoliGraph/ \
        deep-translator \
    && conda run -n poligraph playwright install firefox \
    && conda clean -afy

# ── Entorno virtual webXray ───────────────────────────────────────────────────
RUN python3 -m venv /app/webXray/venv_tfg \
    && /app/webXray/venv_tfg/bin/pip install --no-cache-dir --upgrade pip \
    && /app/webXray/venv_tfg/bin/pip install --no-cache-dir \
        -r /app/webXray/requirements.txt

# ── Dependencias Node.js ──────────────────────────────────────────────────────
RUN cd /app/WEC/website-evidence-collector \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

RUN cd /app/BL/blacklight-collector \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

RUN cd /app/privacy-pioneer-web-crawler/selenium-crawler \
    && npm ci --omit=dev --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

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
