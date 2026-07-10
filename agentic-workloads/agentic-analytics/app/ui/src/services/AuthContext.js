import { createContext, useState, useContext, useCallback } from 'react';
import { getCurrentUser as getStoredUser, isAuthenticated as checkAuth, login as doLogin, logout as doLogout, isLoginAvailable, handleAuthCallback as processCallback } from './authService';

const AuthContext = createContext();

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(getStoredUser());
  const authenticated = checkAuth();

  const refreshUser = useCallback(() => {
    setUser(getStoredUser());
  }, []);

  const login = useCallback(() => doLogin(), []);
  const logout = useCallback(() => doLogout(), []);

  const handleAuthCallback = useCallback(async () => {
    const wasCallback = await processCallback();
    if (wasCallback) refreshUser();
    return wasCallback;
  }, [refreshUser]);

  return (
    <AuthContext.Provider value={{ user, authenticated, login, logout, handleAuthCallback, isLoginAvailable, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
