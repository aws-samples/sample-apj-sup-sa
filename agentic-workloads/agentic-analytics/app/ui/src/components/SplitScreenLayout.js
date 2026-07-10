import React, { useState } from 'react';
import {
  Box,
  Grid,
  Paper,
  ThemeProvider,
  createTheme,
  CssBaseline,
  AppBar,
  Toolbar,
  Typography,
  Avatar,
  Chip,
  Button,
  IconButton,
} from '@mui/material';
import {
  Analytics,
  Business,
  TrendingUp,
  Person,
  Login as LoginIcon,
  Logout as LogoutIcon,
} from '@mui/icons-material';
import ChatPanel from './ChatPanel';
import DataPanel from './DataPanel';
import { useAuth } from '../services/AuthContext';

// Unicorn-themed professional theme
const unicornTheme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#7c3aed',
      light: '#a78bfa',
      dark: '#5b21b6',
    },
    secondary: {
      main: '#ec4899',
      light: '#f472b6',
      dark: '#be185d',
    },
    background: {
      default: '#faf5ff',
      paper: '#ffffff',
    },
    text: {
      primary: '#1e1b4b',
      secondary: '#4c1d95',
    },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    h4: { fontWeight: 600, fontSize: '1.75rem' },
    h6: { fontWeight: 500, fontSize: '1.125rem' },
    body1: { fontSize: '0.95rem', lineHeight: 1.6 },
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: 'none', boxShadow: '0 2px 8px rgba(124,58,237,0.1)' },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: { textTransform: 'none', fontWeight: 500, borderRadius: 8 },
      },
    },
    MuiChip: {
      styleOverrides: { root: { borderRadius: 6 } },
    },
  },
});

const SplitScreenLayout = () => {
  const [activePanel, setActivePanel] = useState('overview');
  const [panelData, setPanelData] = useState(null);
  const { user: currentUser, authenticated, login, logout, isLoginAvailable } = useAuth();

  const businessInfo = {
    name: 'Timely-Unicorn Analytics',
    role: 'Business Intelligence',
    platform: 'Unicorn Rental Platform',
    avatar: 'TU',
  };

  const handlePanelUpdate = (panelType, data = null) => {
    setActivePanel(panelType);
    setPanelData(data);
  };

  return (
    <ThemeProvider theme={unicornTheme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
        <AppBar 
          position="static" 
          elevation={0} 
          sx={{ 
            background: 'linear-gradient(135deg, #7c3aed 0%, #ec4899 100%)',
            borderBottom: 1, 
            borderColor: 'divider',
            flexShrink: 0,
            zIndex: 1200
          }}
        >
          <Toolbar>
            <Avatar sx={{ bgcolor: 'rgba(255,255,255,0.2)', width: 40, height: 40, mr: 2, fontSize: '1rem', fontWeight: 600 }}>
              Unicorn
            </Avatar>
            <Box sx={{ flexGrow: 1 }}>
              <Typography variant="h6" sx={{ color: 'white', mb: 0.5 }}>
                Timely-Unicorn Analytics Assistant
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Chip icon={<Analytics sx={{ color: 'white !important' }} />} label="Business Intelligence" size="small" sx={{ fontSize: '0.75rem', color: 'white', borderColor: 'rgba(255,255,255,0.5)', '& .MuiChip-icon': { color: 'white' } }} variant="outlined" />
                <Chip icon={<Business sx={{ color: 'white !important' }} />} label="Multi-Tenant SaaS" size="small" sx={{ fontSize: '0.75rem', color: 'white', borderColor: 'rgba(255,255,255,0.5)' }} variant="outlined" />
                <Chip icon={<TrendingUp sx={{ color: 'white !important' }} />} label="Real-time Analytics" size="small" sx={{ fontSize: '0.75rem', color: 'white', borderColor: 'rgba(255,255,255,0.5)' }} variant="outlined" />
              </Box>
            </Box>
            {/* User Display + Login/Logout */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 2 }}>
              {authenticated ? (
                <>
                  <Box sx={{ textAlign: 'right' }}>
                    <Typography variant="body2" sx={{ color: 'white', fontWeight: 500 }}>
                      {currentUser.name} ({currentUser.role})
                    </Typography>
                  </Box>
                  <IconButton size="small" onClick={logout} title="Logout" sx={{ color: 'white' }}>
                    <LogoutIcon sx={{ fontSize: 20 }} />
                  </IconButton>
                </>
              ) : (
                <>
                  <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.7)' }}>
                    Guest User
                  </Typography>
                  {isLoginAvailable() && (
                    <Button size="small" startIcon={<LoginIcon />} onClick={login} variant="outlined"
                      sx={{ color: 'white', borderColor: 'rgba(255,255,255,0.5)', fontSize: '0.75rem' }}>
                      Login
                    </Button>
                  )}
                  <Avatar sx={{ bgcolor: 'rgba(255,255,255,0.2)', width: 36, height: 36 }}>
                    <Person sx={{ fontSize: 20 }} />
                  </Avatar>
                </>
              )}
            </Box>
          </Toolbar>
        </AppBar>

        <Box sx={{ flexGrow: 1, overflow: 'hidden', minHeight: 0 }}>
          <Grid container sx={{ height: '100%', overflow: 'hidden' }}>
            <Grid size={{ xs: 12, md: 4 }}>
              <Paper elevation={0} sx={{ height: '100%', borderRadius: 0, borderRight: { md: 1 }, borderColor: 'rgba(124,58,237,0.1)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                <ChatPanel onPanelUpdate={handlePanelUpdate} staffInfo={businessInfo} />
              </Paper>
            </Grid>
            <Grid size={{ xs: 12, md: 8 }}>
              <Paper elevation={0} sx={{ height: '100%', borderRadius: 0, backgroundColor: 'background.default', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                <DataPanel activePanel={activePanel} panelData={panelData} staffInfo={businessInfo} onPanelChange={setActivePanel} />
              </Paper>
            </Grid>
          </Grid>
        </Box>
      </Box>
    </ThemeProvider>
  );
};

export default SplitScreenLayout;
