# Stap 1: Bouwfase - Installeer dependencies en bouw de Next.js app
FROM node:20-alpine AS builder

# Declareer het build-argument zodat we het kunnen gebruiken.
ARG IS_DOCKER_BUILD
ENV IS_DOCKER_BUILD=${IS_DOCKER_BUILD}

# Declareer en gebruik de API URL tijdens de build.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

# Zet de werkdirectory
WORKDIR /app

# Kopieer package.json en package-lock.json
COPY package*.json ./

# Installeer dependencies
RUN npm install

# Kopieer de rest van de applicatiecode
COPY . .

# Bouw de applicatie voor productie
RUN npm run build

# Stap 2: Productiefase - Een slanke server om de gebouwde app te draaien
FROM node:20-alpine

WORKDIR /app

# Kopieer de gebouwde app van de 'builder' fase
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

# Expose de poort waar de app op draait
EXPOSE 3000

# Start de Next.js productie server
CMD ["npm", "start"]