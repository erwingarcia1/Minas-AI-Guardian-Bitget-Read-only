import * as Device from 'expo-device';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { api, describeError, publicApi } from '@/api/client';

import { forget, hasStoredSession, onSessionChange, storeTokens } from './tokens';

type State = 'loading' | 'signed-in' | 'signed-out';

export type SignInResult = { ok: true } | { ok: false; code: string; message: string };

type Session = {
  state: State;
  signIn: (email: string, password: string, code: string) => Promise<SignInResult>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>('loading');

  useEffect(() => {
    let live = true;
    hasStoredSession().then((stored) => live && setState(stored ? 'signed-in' : 'signed-out'));
    const unsubscribe = onSessionChange((signedIn) => setState(signedIn ? 'signed-in' : 'signed-out'));
    return () => {
      live = false;
      unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string, code: string): Promise<SignInResult> => {
    // What the member sees on their device list and in the sign-in email.
    const deviceName = Device.deviceName ?? Device.modelName ?? undefined;
    try {
      const { data, error, response } = await publicApi.POST('/api/v1/session', {
        body: { email: email.trim(), password, code: code.trim(), device_name: deviceName },
      });
      if (data) {
        await storeTokens(data);
        return { ok: true };
      }
      return { ok: false, code: error?.error ?? 'unknown', message: describeError(error, response) };
    } catch {
      return { ok: false, code: 'network', message: describeError(undefined) };
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.DELETE('/api/v1/session');
    } catch {
      // Offline: the phone forgets anyway. The server-side sign-in then ends
      // when its refresh token goes unused, or from the portal's Security page.
    }
    await forget();
  }, []);

  const value = useMemo(() => ({ state, signIn, signOut }), [state, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) throw new Error('useSession outside SessionProvider');
  return session;
}
