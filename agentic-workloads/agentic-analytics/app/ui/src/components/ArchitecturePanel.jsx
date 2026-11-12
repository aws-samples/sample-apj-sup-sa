import React, { useState } from 'react';
import { Box, Typography, Chip, Stack } from '@mui/material';

/**
 * ArchitecturePanel: shows the system architecture diagram (source:
 * workshop/static/images/full-architecture.drawio, copied to public/architecture.png).
 * Click to open full size.
 */
const ArchitecturePanel = () => {
  const [zoomed, setZoomed] = useState(false);

  const tech = [
    'Amazon Cognito', 'AgentCore Gateway (front door)', 'AWS WAF',
    'AgentCore Runtime', 'Claude Sonnet 5.5', 'Bedrock Guardrails', 'AgentCore Memory',
    'Code Interpreter', 'AgentCore Gateway (MCP)', 'AgentCore Policy (Cedar)',
    'AgentCore Web Search', 'Bedrock Knowledge Base (managed)', 'AWS Glue',
    'Aurora PostgreSQL (RLS)', 'AWS Step Functions', 'Pipecat + Deepgram (voice)',
  ];

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Text and voice both enter through a front-door gateway that checks your Cognito
        token, applies AWS WAF and a per-user rate limit, then calls a Strands agent on
        AgentCore Runtime (Claude Sonnet 5.5). The agent reaches its tools through a second
        gateway, where Cedar policies decide what your role may call and the database
        filters rows to your tenant. A schema pipeline keeps the agent's view of the
        database current.
      </Typography>

      <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: 2 }}>
        {tech.map((t) => (
          <Chip key={t} label={t} size="small" variant="outlined"
            sx={{ fontSize: '0.7rem', borderColor: 'divider' }} />
        ))}
      </Stack>

      <Box
        component="img"
        src={`${process.env.PUBLIC_URL || ''}/architecture.png`}
        alt="Voice Analytics Agent — Cascaded Pipeline Architecture"
        onClick={() => setZoomed(true)}
        sx={{
          width: '100%', height: 'auto', borderRadius: 1,
          border: 1, borderColor: 'divider', cursor: 'zoom-in',
          boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
        }}
      />
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Click the diagram to view it full size.
      </Typography>

      {zoomed && (
        <Box
          onClick={() => setZoomed(false)}
          sx={{
            position: 'fixed', inset: 0, zIndex: 1400, cursor: 'zoom-out',
            backgroundColor: 'rgba(0,0,0,0.8)', display: 'flex',
            alignItems: 'center', justifyContent: 'center', p: 4,
          }}
        >
          <Box component="img" src={`${process.env.PUBLIC_URL || ''}/architecture.png`}
            alt="Architecture (full size)"
            sx={{ maxWidth: '100%', maxHeight: '100%', borderRadius: 1, backgroundColor: '#fff' }} />
        </Box>
      )}
    </Box>
  );
};

export default ArchitecturePanel;
