# Build stage: compilers and -dev headers are only needed to build wheels
FROM python:3.12-slim AS build

WORKDIR /build

RUN apt-get update &&     apt-get install -y --no-install-recommends         gcc         pkg-config         libpq-dev         libcairo2-dev         libffi-dev &&     rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Runtime stage: only the shared libraries the app loads (Cairo/Pango for cairosvg,
# LibreOffice for PDF conversion); no compilers
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update &&     apt-get install -y --no-install-recommends         libcairo2         libpango-1.0-0         libpangocairo-1.0-0         libgdk-pixbuf-2.0-0         shared-mime-info         libreoffice-writer         libreoffice-common &&     rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl &&     rm -rf /wheels

COPY . .

RUN mkdir -p /app/flask_session

EXPOSE 5100

CMD ["gunicorn",      "--bind", "0.0.0.0:5100",      "--workers", "3",      "--worker-class", "gthread",      "--threads", "4",      "--timeout", "120",      "--access-logfile", "-",      "--error-logfile", "-",      "run:app"]
