import React from 'react';
import { createRoot } from 'react-dom/client';
import './aws-config.js';
import App from './App.jsx';
import '@aws-amplify/ui-react/styles.css';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
