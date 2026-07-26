import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** @type {import('tailwindcss').Config} */
const config = {
  content: [
    path.join(__dirname, './pages/**/*.{js,ts,jsx,tsx,mdx}'),
    path.join(__dirname, './components/**/*.{js,ts,jsx,tsx,mdx}'),
    path.join(__dirname, './app/**/*.{js,ts,jsx,tsx,mdx}'),
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;