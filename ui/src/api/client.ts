import axios from 'axios';

// Use relative path - Vite proxy handles the rest
export const api = axios.create({
  baseURL: '/api',
});
