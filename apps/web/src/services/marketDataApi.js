import api from './api.js';

const data = (response) => response.data;
const resourcePath = (resource, id) => `/${resource}/${encodeURIComponent(id)}`;

// API methods follow docs/api-contract-frontend.md on
// feature/hieu-auth-api-ai-report.
export const marketDataApi = {
  getCurrentUser: (config) => api.get('/me', config).then(data),
  getDashboardStats: () => api.get('/dashboard/stats').then(data),
  getAlerts: (params) => api.get('/alerts', { params }).then(data),
  getSources: (params) => api.get('/sources', { params }).then(data),
  createSource: (payload) => api.post('/sources', payload).then(data),
  updateSource: (sourceId, payload) =>
    api.patch(resourcePath('sources', sourceId), payload).then(data),
  deleteSource: (sourceId) =>
    api.delete(resourcePath('sources', sourceId)).then(data),
  createUploadJob: (payload) => api.post('/jobs', payload).then(data),
  getUploadJobStatus: (jobId) =>
    api.get(`${resourcePath('jobs', jobId)}/status`).then(data),
  getPriceMatrix: (params) => api.get('/matrix', { params }).then(data),
  getProducts: (params) => api.get('/products', { params }).then(data),
  createProduct: (payload) => api.post('/products', payload).then(data),
  matchProduct: (productId, payload) =>
    api.patch(`${resourcePath('products', productId)}/match`, payload).then(data),
  getPriceHistory: (productId, params) =>
    api.get(`${resourcePath('products', productId)}/price-history`, { params }).then(data),
  getReports: (params) => api.get('/reports', { params }).then(data),
  getReport: (reportId) => api.get(resourcePath('reports', reportId)).then(data),
  triggerReport: () => api.post('/reports/trigger').then(data),
  getMembers: (params) => api.get('/members', { params }).then(data),
  inviteMember: (payload) => api.post('/members/invite', payload).then(data),
  updateMember: (userId, payload) =>
    api.patch(resourcePath('members', userId), payload).then(data),
};
