import * as Device from 'expo-device';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { describeError, publicApi } from '@/api/client';
import { isTimeout } from '@/api/network';

import { type Ending, hasStoredSession, onSessionChange, parseTokens, signOut as endSession, storeTokens } from './tokens';

type State = 'loading' | 'signed-in' | 'signed-out';

export type SignInResult = { ok: true } | { ok: false; code: string; message: string };

type Session = {
  state: State;
  /** The server ended the last sign-in (rather than the member signing out),
   * so the sign-in screen can say so. */
  ended: boolean;
  signIn: (email: string, password: string, code: string) => Promise<SignInResult>;
  /** Start the sign-in an answer carries - accepting an invitation, or
   * turning two-factor on - once its recovery codes have been shown. False
   * when the answer is not a token pair. */
  adopt: (answer: unknown) => Promise<boolean>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>('loading');
  const [ending, setEnding] = useState<Ending | null>(null);

  useEffect(() => {
    let live = true;
    // Never rejects - a store that cannot be read counts as no sign-in - so
    // the app always leaves its splash screen.
    hasStoredSession().then((stored) => live && setState(stored ? 'signed-in' : 'signed-out'));
    const unsubscribe = onSessionChange((signedIn, how) => {
      setState(signedIn ? 'signed-in' : 'signed-out');
      setEnding(signedIn ? null : (how ?? null));
    });
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
        const tokens = parseTokens(data);
        if (tokens === null) {
          return { ok: false, code: 'unreadable', message: describeError(undefined, undefined, new SyntaxError()) };
        }
        await storeTokens(tokens);
        return { ok: true };
      }
      if (response.status === 404 && typeof error !== 'object') {
        // Not the API's 404 (that one is JSON): a server without the app API.
        return { ok: false, code: 'not_found', message: "This server doesn't offer the app's sign-in." };
      }
      return { ok: false, code: error?.error ?? 'unknown', message: describeError(error, response) };
    } catch (thrown) {
      if (isTimeout(thrown)) {
        // Minas may have signed the phone in, and spent the code, before the
        // answer was lost: the same code will not work twice.
        return {
          ok: false,
          code: 'timeout',
          message:
            "Minas didn't answer in time. Your sign-in may still have gone through: wait for the next code in your " +
            'authenticator app before you try again.',
        };
      }
      return { ok: false, code: 'network', message: describeError(undefined, undefined, thrown) };
    }
  }, []);

  const adopt = useCallback(async (answer: unknown): Promise<boolean> => {
    // Stored only now, after the codes: storing the pair announces the
    // sign-in, and the layout then takes every signed-out screen away -
    // the recovery codes with them.
    const tokens = parseTokens(answer);
    if (tokens === null) return false;
    await storeTokens(tokens);
    return true;
  }, []);

  const signOut = useCallback(() => endSession(), []);

  const value = useMemo(
    () => ({ state, ended: ending === 'ended', signIn, adopt, signOut }),
    [state, ending, signIn, adopt, signOut],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) throw new Error('useSession outside SessionProvider');
  return session;
}
