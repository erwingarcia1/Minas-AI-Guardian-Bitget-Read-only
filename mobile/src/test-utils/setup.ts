/// <reference types="jest" />
// Run before every test file (package.json jest.setupFiles).

// The secure store, as a Map. Jest's automatic mock would answer `undefined`
// to every read, which the app takes for a stored sign-in.
jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
  };
});

// What the phone calls itself, for the device list and the sign-in email.
jest.mock('expo-device', () => ({ deviceName: 'Test phone', modelName: 'Test model' }));
