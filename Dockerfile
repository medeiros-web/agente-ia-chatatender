FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/dados.sqlite

EXPOSE 5000

CMD ["python", "app.py"]
