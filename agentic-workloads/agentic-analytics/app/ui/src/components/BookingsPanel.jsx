import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import { CalendarMonth } from '@mui/icons-material';

const BookingsPanel = ({ panelData }) => (
  <Box sx={{ p: 3 }}>
    <Paper elevation={0} sx={{ p: 3, border: 1, borderColor: 'divider', borderRadius: 2, textAlign: 'center' }}>
      <CalendarMonth sx={{ fontSize: 48, color: 'primary.main', mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1 }}>Booking Analytics</Typography>
      <Typography variant="body2" color="text.secondary">
        Ask the chat assistant about booking trends, popular rental periods, cancellation rates, and booking patterns.
      </Typography>
      <Box sx={{ mt: 3, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
        <Typography variant="caption" color="text.secondary">
          Try: "Show recent bookings" or "What are the most popular rental durations?"
        </Typography>
      </Box>
    </Paper>
  </Box>
);

export default BookingsPanel;
