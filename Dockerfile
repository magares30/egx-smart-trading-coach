FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
COPY requirements-talib.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN (pip install --no-cache-dir --only-binary=:all: -r requirements-talib.txt \
    && python -c "import talib; print('TA-Lib import check: ACTIVE')" \
    ) || echo "WARNING: Optional TA-Lib install/import failed; reports will use runtime fallback."

COPY . .

RUN mkdir -p data/reports storage

EXPOSE 8080

CMD ["python", "main.py", "--telegram-bot"]
