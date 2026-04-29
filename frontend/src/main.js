import { jsx as _jsx } from "react/jsx-runtime";
import React from 'react';
import ReactDOM from 'react-dom/client';
import { AuthProvider } from 'react-oidc-context';
import { oidcConfig } from './auth/config';
import 'bootstrap/dist/css/bootstrap.min.css';
import App from './App';
ReactDOM.createRoot(document.getElementById('root')).render(_jsx(React.StrictMode, { children: _jsx(AuthProvider, { ...oidcConfig, children: _jsx(App, {}) }) }));
