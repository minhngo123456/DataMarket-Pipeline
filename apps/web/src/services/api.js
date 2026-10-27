import axios from 'axios';
import { fetchAuthSession } from 'aws-amplify/auth';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:3001',
  timeout: 10000,
});

api.interceptors.request.use(async (config) => {
  try {
    const session = await fetchAuthSession();
    const idToken = session.tokens?.idToken?.toString();

    if (idToken) {
      config.headers.Authorization = `Bearer ${idToken}`;
    }
  } catch {
    // If there is no signed-in session, the API can respond with 401.
  }

  return config;
});

export default api;
