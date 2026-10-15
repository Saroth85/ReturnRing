// Where the live API is: the same variable the dev server and `vite preview` proxy to.
export const API_URL = (process.env.RETURNRING_API_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, '');
