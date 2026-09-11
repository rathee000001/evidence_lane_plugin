import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/studio/',
  plugins: [react()],
  build: {
    outDir: '../../.work/studio-dist', emptyOutDir: true, assetsInlineLimit: 0,
    rollupOptions: { output: { entryFileNames: 'app.js', assetFileNames: asset => asset.name?.endsWith('.css') ? 'styles.css' : 'assets/[name]-[hash][extname]' } },
  },
});
