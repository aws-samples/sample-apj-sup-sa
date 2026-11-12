import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import { Pets } from '@mui/icons-material';

const UnicornsPanel = ({ panelData }) => (
  <Box sx={{ p: 3 }}>
    <Paper elevation={0} sx={{ p: 3, border: 1, borderColor: 'divider', borderRadius: 2, textAlign: 'center' }}>
      <Pets sx={{ fontSize: 48, color: 'primary.main', mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1 }}>Unicorn Fleet Analytics</Typography>
      <Typography variant="body2" color="text.secondary">
        Ask the chat assistant about unicorn availability, breed performance, maintenance needs, and utilization rates.
      </Typography>
      <Box sx={{ mt: 3, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
        <Typography variant="caption" color="text.secondary">
          Try: "Which unicorn breeds generate most revenue?" or "What unicorns need maintenance?"
        </Typography>
      </Box>
    </Paper>
  </Box>
);

export default UnicornsPanel;
