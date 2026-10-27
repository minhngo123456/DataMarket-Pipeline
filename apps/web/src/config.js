// Sample data is only used when the API cannot be reached, and every page that
// shows it says so. Never enable this flag for a deployed build.
export const mockFallbackEnabled =
  import.meta.env.DEV ||
  import.meta.env.VITE_ENABLE_MOCK_DATA === 'true' ||
  import.meta.env.VITE_ENABLE_MOCK_USER === 'true';
