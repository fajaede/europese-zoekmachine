import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Vertel Turbopack wat de root van het project is om de waarschuwing
  // over meerdere lockfiles te onderdrukken.
  // We stellen dit in op de huidige map (de root van de Next.js app).
  experimental: {
    turbopack: {
      root: __dirname,
    },
  },
  // Asynchrone rewrites om API-verzoeken te proxyen.
  // Dit zorgt ervoor dat de frontend (op poort 3000) kan communiceren
  // met de backend (op poort 18000) zonder CORS-problemen.
  async rewrites() {
    // Bepaal de doel-URL voor de API.
    // Gebruik de omgevingsvariabele API_URL als deze is ingesteld,
    // anders val terug op de lokale ontwikkelserver.
    const apiUrl = process.env.API_URL || 'http://127.0.0.1:18000';

    return [
      {
        source: '/api/:path*',
        destination: `${apiUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;