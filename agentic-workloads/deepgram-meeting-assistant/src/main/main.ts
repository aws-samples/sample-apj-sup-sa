/**
 * Main Process Entry Point
 * 
 * Electron 앱의 메인 프로세스 진입점입니다.
 * 앱 라이프사이클 이벤트 핸들러만 포함하고, 
 * 다른 로직은 서비스와 핸들러로 위임합니다.
 * 
 * ORCH-014: main.ts Mixed Responsibilities → 앱 라이프사이클만 유지
 */

import { app, BrowserWindow, ipcMain } from 'electron';
import fs from 'node:fs';
import path from 'path';
import { IPC_CHANNELS } from '../shared/constants/ipc-channels';
import {
  registerMeetingHandlers,
  registerMcpHandlers,
  registerVocabularyHandlers,
  registerAgentHandlers,
  initializeDatabase,
  closeDatabaseConnection,
} from './ipc';
import { mcpClientService, settingsService, windowService } from './services';

// ============================================================================
// Electron Squirrel Startup Handler
// ============================================================================

if (require('electron-squirrel-startup')) {
  app.quit();
}

// ============================================================================
// Vite Constants
// ============================================================================

declare const MAIN_WINDOW_VITE_DEV_SERVER_URL: string;
declare const MAIN_WINDOW_VITE_NAME: string;

// ============================================================================
// Window Configuration
// ============================================================================

function getWindowConfig() {
  const rendererBasePath = path.join(__dirname, '../renderer', MAIN_WINDOW_VITE_NAME);
  const rendererCandidates = [
    path.join(rendererBasePath, 'index.html'),
    path.join(rendererBasePath, 'src/renderer/index.html'),
  ];
  const rendererPath =
    rendererCandidates.find((candidate) => fs.existsSync(candidate)) ?? rendererCandidates[0];

  return {
    devServerUrl: MAIN_WINDOW_VITE_DEV_SERVER_URL,
    preloadPath: path.join(__dirname, 'preload.js'),
    rendererPath,
  };
}

// ============================================================================
// App Lifecycle Events
// ============================================================================

app.on('ready', () => {
  initializeDatabase();
  windowService.createWindow(getWindowConfig());
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    closeDatabaseConnection();
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    windowService.createWindow(getWindowConfig());
  }
});

app.on('before-quit', async () => {
  // MCP 연결 해제 (Requirements: 2.2)
  await mcpClientService.disconnect();
  closeDatabaseConnection();
});

// ============================================================================
// Settings IPC Handlers
// ============================================================================

ipcMain.handle(IPC_CHANNELS.SETTINGS_SAVE, async (_event, settings: unknown) => {
  return settingsService.saveSettings(settings);
});

ipcMain.handle(IPC_CHANNELS.SETTINGS_LOAD, async () => {
  return settingsService.loadSettings();
});

ipcMain.handle(IPC_CHANNELS.SETTINGS_CLEAR, async () => {
  return settingsService.clearSettings();
});

ipcMain.handle(IPC_CHANNELS.SETTINGS_GET_AWS_CREDENTIALS, async () => {
  return settingsService.getAWSCredentials();
});

// ============================================================================
// Register Handlers
// ============================================================================

registerMeetingHandlers(
  () => settingsService.getCredentials(),
  () => settingsService.getSettings()
);
registerMcpHandlers();
registerVocabularyHandlers();
registerAgentHandlers(
  () => settingsService.getCredentials(),
  () => settingsService.getSettings()
);
