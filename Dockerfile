# Stage 1: build the React front end
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: API + OCR engine (Tesseract via OCRmyPDF)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ocrmypdf tesseract-ocr tesseract-ocr-eng ghostscript qpdf unpaper pngquant \
    && rm -rf /var/lib/apt/lists/*
# add more OCR languages by extending: tesseract-ocr-tam tesseract-ocr-hin ...
WORKDIR /app/backend
COPY backend/requirements.txt ./
# ocrmypdf is installed by apt (system python); don't pip-install it again
RUN grep -v '^ocrmypdf' requirements.txt > r.txt && pip install --no-cache-dir -r r.txt
COPY backend/app ./app
COPY --from=web /web/dist /app/frontend/dist
ENV PDFDESK_DATA=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
