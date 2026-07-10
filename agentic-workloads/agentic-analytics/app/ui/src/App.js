import { useEffect, useState } from 'react';
import SplitScreenLayout from './components/SplitScreenLayout';
import { AuthProvider, useAuth } from './services/AuthContext';

function AppContent() {
  const { handleAuthCallback } = useAuth();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const init = async () => {
      await handleAuthCallback();
      setReady(true);
    };
    init();
  }, [handleAuthCallback]);

  if (!ready) return null;
  return <SplitScreenLayout />;
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;
