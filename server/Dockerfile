FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir flask==3.1.* pillow==11.* gunicorn==23.*
COPY app.py .
COPY static ./static
EXPOSE 8090
CMD ["gunicorn", "-b", "0.0.0.0:8090", "-w", "2", "app:app"]
