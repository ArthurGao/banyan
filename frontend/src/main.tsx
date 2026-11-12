import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from '@/app/App';
import { applyTheme, storedTheme } from '@/lib/theme';
import '@/styles.css';

// index.html already did this before the first paint; this keeps lib/theme.ts the authority.
applyTheme(storedTheme());

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element #root is missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
