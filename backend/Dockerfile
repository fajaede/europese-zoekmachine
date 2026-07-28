# Gebruik een officiële, slanke Python-image als basis
FROM python:3.12-slim

# Voorkom dat Python .pyc-bestanden schrijft
ENV PYTHONDONTWRITEBYTECODE 1
# Zorg ervoor dat Python-output direct wordt weergegeven (nuttig voor logs)
ENV PYTHONUNBUFFERED 1

# Stel de werkdirectory in binnen de container
WORKDIR /app

# Kopieer de dependency-bestanden
COPY requirements.txt .

# Installeer de dependencies
# We gebruiken --no-cache-dir om de image-grootte te beperken
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer de rest van de applicatiecode naar de werkdirectory
COPY . .

# De poort die Gunicorn zal gebruiken binnen de container
EXPOSE 18000

# Het commando om de applicatie te starten wanneer de container wordt gestart
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:18000", "main:app"]