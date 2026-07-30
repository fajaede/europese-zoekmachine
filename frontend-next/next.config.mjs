import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'image.thum.io',
      },
      {
        protocol: 'https',
        hostname: 'images.unsplash.com',
      },
    ],
  },

  experimental: {
    // Deze instelling vertelt Next.js waar het moet beginnen met het zoeken naar
    // bestanden die nodig zijn voor de build. In een monorepo-structuur
    // moet dit verwijzen naar de root van de hele repository.
    outputFileTracingRoot: path.join(__dirname, '../../'),

    // Stel de root voor Turbopack expliciet in op dezelfde waarde om de
    // waarschuwing 'Both outputFileTracingRoot and turbopack.root are set'
    // op te lossen.
    turbopack: {
      root: path.join(__dirname, '../../'),
    },
  },

  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:18000'}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;