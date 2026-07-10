import React from 'react';
import { Box, Paper, Typography, Grid } from '@mui/material';
import { TrendingUp, People, Pets, CalendarMonth, AttachMoney, Business } from '@mui/icons-material';

const StatCard = ({ icon, title, value, subtitle, color = 'primary.main' }) => (
  <Paper elevation={0} sx={{ p: 3, border: 1, borderColor: 'divider', borderRadius: 2 }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
      <Box sx={{ p: 1.5, borderRadius: 2, bgcolor: `${color}15` }}>{React.cloneElement(icon, { sx: { color, fontSize: 28 } })}</Box>
      <Box>
        <Typography variant="body2" color="text.secondary">{title}</Typography>
        <Typography variant="h5" fontWeight={600}>{value}</Typography>
        {subtitle && <Typography variant="caption" color="text.secondary">{subtitle}</Typography>}
      </Box>
    </Box>
  </Paper>
);

const OverviewPanel = ({ staffInfo }) => {
  const stats = [
    { icon: <Business />, title: 'Rental Businesses', value: '2', subtitle: 'Active accounts', color: '#7c3aed' },
    { icon: <Pets />, title: 'Unicorns', value: '100', subtitle: 'Across all breeds', color: '#ec4899' },
    { icon: <People />, title: 'Customers', value: '500', subtitle: 'Individual & organization', color: '#3b82f6' },
    { icon: <CalendarMonth />, title: 'Bookings', value: '13,912', subtitle: 'Total rentals', color: '#10b981' },
    { icon: <AttachMoney />, title: 'Transactions', value: '13,912', subtitle: 'Payment records', color: '#f59e0b' },
    { icon: <TrendingUp />, title: 'Availability', value: '30,735', subtitle: 'Schedule records', color: '#6366f1' },
  ];

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h6" sx={{ mb: 3, display: 'flex', alignItems: 'center', gap: 1 }}>
        Welcome to Timely-Unicorn Analytics
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 4 }}>
        This dashboard provides insights into your unicorn rental business. Use the chat to ask questions about revenue, customers, unicorn performance, and more.
      </Typography>
      
      <Grid container spacing={2}>
        {stats.map((stat, idx) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={idx}>
            <StatCard {...stat} />
          </Grid>
        ))}
      </Grid>

      <Paper elevation={0} sx={{ mt: 4, p: 3, border: 1, borderColor: 'divider', borderRadius: 2, bgcolor: 'primary.50' }}>
        <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 2 }}>Try asking:</Typography>
        <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
          <li><Typography variant="body2">"Show me the top 5 customers by revenue"</Typography></li>
          <li><Typography variant="body2">"What are the monthly revenue trends?"</Typography></li>
          <li><Typography variant="body2">"Which unicorn breeds generate the most revenue?"</Typography></li>
          <li><Typography variant="body2">"Show customers at risk of churning"</Typography></li>
          <li><Typography variant="body2">"What's the customer segmentation breakdown?"</Typography></li>
        </Box>
      </Paper>
    </Box>
  );
};

export default OverviewPanel;
