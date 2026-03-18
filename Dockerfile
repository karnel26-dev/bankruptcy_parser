FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt — для Docker
# pyproject.toml + poetry.lock — для локальной разработки
RUN pip config set global.trusted-host "pypi.org files.pythonhosted.org" \
    && pip config set global.timeout 120

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data /logs

ENTRYPOINT ["python", "main.py"]
CMD ["--input", "/data/inn_list.xlsx"]
