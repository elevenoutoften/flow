FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLOW_DATA_DIR=/data \
    FLOW_HOST=0.0.0.0 \
    FLOW_PORT=8100

WORKDIR /app

COPY pyproject.toml README.md ./
COPY flow_app ./flow_app

RUN pip install --no-cache-dir .

EXPOSE 8100

CMD ["python", "-m", "uvicorn", "flow_app.main:app", "--host", "0.0.0.0", "--port", "8100"]