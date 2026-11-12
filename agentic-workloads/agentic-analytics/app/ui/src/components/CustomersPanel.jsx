import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import { People } from '@mui/icons-material';

const CustomersPanel = ({ panelData }) => (
  <Box sx={{ p: 3 }}>
    <Paper elevation={0} sx={{ p: 3, border: 1, borderColor: 'divider', borderRadius: 2, textAlign: 'center' }}>
      <People sx={{ fontSize: 48, color: 'primary.main', mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1 }}>Customer Analytics</Typography>
      <Typography variant="body2" color="text.secondary">
        Ask the chat assistant about customer segmentation, churn risk, lifetime value, and customer behavior patterns.
      </Typography>
      <Box sx={{ mt: 3, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
        <Typography variant="caption" color="text.secondary">
          Try: "Show customer segmentation breakdown" or "Which customers are at risk of churning?"
        </Typography>
      </Box>
    </Paper>
  </Box>
);

export default CustomersPanel;
