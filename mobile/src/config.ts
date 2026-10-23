// Where the API lives: the project's one public address (decision 22). A
// developer points the app elsewhere with EXPO_PUBLIC_API_URL, which Expo reads
// at build time - e.g. EXPO_PUBLIC_API_URL=http://192.168.1.20:8391 npm start.
export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? 'https://guardian.minastrade.com').replace(/\/+$/, '');
