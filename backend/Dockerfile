# In de map 'backend-fastapi'
FROM python:3.11-slim

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt
COPY ./healthcheck.sh /usr/local/bin/healthcheck.sh

# Installeer curl voor de healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# Maak het healthcheck script uitvoerbaar
RUN chmod +x /usr/local/bin/healthcheck.sh

COPY ./ /code/

# Zorg dat de poort overeenkomt met wat je in de code gebruikt
EXPOSE 18000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18000"]
