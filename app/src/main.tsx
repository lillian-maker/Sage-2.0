import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './assets/fonts/fonts.css';
import './tokens/tokens.css';
import './theme/theme.css';
import './styles/global.css';
import { App } from './App';

const root = document.getElementById('root');
if (!root) throw new Error('缺少 #root 节点');
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
