import { createContext, useState, useContext, useCallback, useEffect } from 'react';
import { getCurrentUser as getStoredUser, isAuthenticated as checkAuth, login as doLogin, logout as doLogout, isLoginAvailable, handleAuthCallback as processCallback, refreshTokens } from './authService';

const REFRESH_EARLY_MS = 5 * 60 * 1000;
const CHECK_EVERY_MS = 60 * 1000;

const AuthContext = createContext();

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(getStoredUser());
  const [authenticated, setAuthenticated] = useState(checkAuth());

  // Cognito access tokens last an hour. Renew them with the refresh token shortly before they
  // expire (and on load, if the page was closed past expiry), so a long session stays signed in.
  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      const expiry = parseInt(localStorage.getItem('cognito_token_expiry') || '0', 10);
      if (expiry && Date.now() > expiry - REFRESH_EARLY_MS) await refreshTokens();
      if (!stopped) setAuthenticated(checkAuth());
    };
    tick();
    const id = setInterval(tick, CHECK_EVERY_MS);
    return () => { stopped = true; clearInterval(id); };
  }, []);

  const refreshUser = useCallback(() => {
    setUser(getStoredUser());
  }, []);

  const login = useCallback(() => doLogin(), []);
  const logout = useCallback(() => doLogout(), []);

  const handleAuthCallback = useCallback(async () => {
    const wasCallback = await processCallback();
    if (wasCallback) { refreshUser(); setAuthenticated(checkAuth()); }
    return wasCallback;
  }, [refreshUser]);

  return (
    <AuthContext.Provider value={{ user, authenticated, login, logout, handleAuthCallback, isLoginAvailable, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
