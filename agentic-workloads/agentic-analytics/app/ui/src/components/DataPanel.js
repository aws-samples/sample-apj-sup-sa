import React from 'react';
import { Box, Typography, Tabs, Tab } from '@mui/material';
import { Dashboard, TrendingUp, People, Pets, CalendarMonth, AccountTree } from '@mui/icons-material';
import OverviewPanel from './OverviewPanel';
import RevenuePanel from './RevenuePanel';
import CustomersPanel from './CustomersPanel';
import UnicornsPanel from './UnicornsPanel';
import BookingsPanel from './BookingsPanel';
import ArchitecturePanel from './ArchitecturePanel';

const DataPanel = ({ activePanel, panelData, staffInfo, onPanelChange }) => {
  const panels = [
    { id: 'overview', label: 'Overview', icon: <Dashboard />, component: <OverviewPanel staffInfo={staffInfo} /> },
    { id: 'revenue', label: 'Revenue', icon: <TrendingUp />, component: <RevenuePanel panelData={panelData} /> },
    { id: 'customers', label: 'Customers', icon: <People />, component: <CustomersPanel panelData={panelData} /> },
    { id: 'unicorns', label: 'Unicorns', icon: <Pets />, component: <UnicornsPanel panelData={panelData} /> },
    { id: 'bookings', label: 'Bookings', icon: <CalendarMonth />, component: <BookingsPanel panelData={panelData} /> },
    { id: 'architecture', label: 'Architecture', icon: <AccountTree />, component: <ArchitecturePanel /> },
  ];

  const activeIndex = Math.max(0, panels.findIndex(p => p.id === activePanel));
  const currentPanel = panels[activeIndex];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <Box sx={{ borderBottom: 1, borderColor: 'divider', backgroundColor: 'background.paper', flexShrink: 0 }}>
        <Box sx={{ px: 3, py: 2 }}>
          <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            {currentPanel.icon} {currentPanel.label}
          </Typography>
          <Typography variant="body2" color="text.secondary">Timely-Unicorn Analytics Dashboard</Typography>
        </Box>
        <Tabs value={activeIndex} onChange={(e, v) => onPanelChange(panels[v].id)} variant="scrollable" scrollButtons="auto"
          sx={{ minHeight: 48, '& .MuiTab-root': { minHeight: 48, textTransform: 'none', fontSize: '0.875rem', fontWeight: 500 } }}>
          {panels.map((panel) => (
            <Tab key={panel.id} icon={panel.icon} label={panel.label} iconPosition="start" sx={{ gap: 1 }} />
          ))}
        </Tabs>
      </Box>
      <Box sx={{ flexGrow: 1, overflow: 'auto', minHeight: 0 }}>{currentPanel.component}</Box>
    </Box>
  );
};

export default DataPanel;
