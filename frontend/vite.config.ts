import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react-swc';
import { defineConfig } from 'vite';

// Dev server: 127.0.0.1:8810 (registered in the machine KB ports registry; 8800 is the backend).
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 8810,
    strictPort: true,
    // Same origin as in production: the API (`banyan serve`) runs on 8800.
    proxy: { '/v1': 'http://127.0.0.1:8800', '/health': 'http://127.0.0.1:8800' },
  },
  preview: {
    host: '127.0.0.1',
    port: 8811,
    strictPort: true,
  },
});
