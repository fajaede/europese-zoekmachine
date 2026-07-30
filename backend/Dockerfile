# Gebruik een officiële, slanke Python-image als basis
FROM python:3.12-slim

# Voorkom dat Python .pyc-bestanden schrijft
ENV PYTHONDONTWRITEBYTECODE 1
# Zorg ervoor dat Python-output direct wordt weergegeven (nuttig voor logs)
ENV PYTHONUNBUFFERED 1

# Stel de werkdirectory in voor de applicatiecode
WORKDIR /app/backend

# Kopieer de dependency-bestanden
COPY backend/requirements.txt .

# Installeer de dependencies
# We gebruiken --no-cache-dir om de image-grootte te beperken
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer de backend- en api-mappen naar de container
COPY backend/ /app/backend/
COPY api/ /app/api/

# Voeg de root van de app toe aan de PYTHONPATH zodat 'api' gevonden kan worden
ENV PYTHONPATH "${PYTHONPATH}:/app"

# De poort die Gunicorn zal gebruiken binnen de container
EXPOSE 18000

# Het commando om de applicatie te starten wanneer de container wordt gestart
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:18000", "main:app"]