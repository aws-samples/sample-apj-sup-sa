import React, { useState } from 'react';
import { Box, Typography, Chip, Stack } from '@mui/material';

/**
 * ArchitecturePanel — shows the voice-analytics agent's system architecture
 * diagram (rendered offline via the AWS diagram tooling, served from
 * /architecture.png in the client's public/ folder). Click to open full size.
 */
const ArchitecturePanel = () => {
  const [zoomed, setZoomed] = useState(false);

  const tech = [
    'Daily (WebRTC)', 'Deepgram STT — Nova-3', 'Deepgram TTS — Aura-2',
    'Pipecat Cloud', 'Amazon Cognito', 'Bedrock AgentCore Runtime',
    'Claude Opus 4.8', 'AgentCore Gateway (MCP)', 'Code Interpreter',
    'AgentCore Memory', 'Aurora PostgreSQL', 'Amazon S3', 'Bedrock Knowledge Base',
  ];

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Cascaded voice pipeline: your speech is streamed over WebRTC to a Pipecat bot,
        transcribed by Deepgram, reasoned over by a Strands agent on Bedrock AgentCore
        (Claude Opus 4.8) with analytics tools, then spoken back via Deepgram TTS while
        the full answer and any charts are shown here.
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
