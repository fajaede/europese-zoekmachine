/** @type {import('next').NextConfig} */
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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

  // This rewrites rule acts as a proxy for server-side requests (both client and server).
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        // Use the runtime API_URL from the environment, with a fallback for local development.
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:18000'}/api/:path*`,
      },
    ];
  },

  turbopack: {
    // Set the root directory for Turbopack to the current directory (__dirname).
    // This ensures that module resolution (e.g., for tailwindcss) starts from
    // the 'frontend-next' folder, not the parent 'europese-zoekmachine' folder.
    root: __dirname,
  },

  experimental: {
  },
};

export default nextConfig;