import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import { TrendingUp } from '@mui/icons-material';

const RevenuePanel = ({ panelData }) => (
  <Box sx={{ p: 3 }}>
    <Paper elevation={0} sx={{ p: 3, border: 1, borderColor: 'divider', borderRadius: 2, textAlign: 'center' }}>
      <TrendingUp sx={{ fontSize: 48, color: 'primary.main', mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1 }}>Revenue Analytics</Typography>
      <Typography variant="body2" color="text.secondary">
        Ask the chat assistant about revenue trends, top customers by revenue, transaction summaries, and financial insights.
      </Typography>
      <Box sx={{ mt: 3, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
        <Typography variant="caption" color="text.secondary">
          Try: "What are the monthly revenue trends?" or "Show top 5 customers by revenue"
        </Typography>
      </Box>
    </Paper>
  </Box>
);

export default RevenuePanel;
