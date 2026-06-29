/**
 * Window Service
 * 
 * 윈도우 생성 및 CSP 설정을 담당하는 서비스입니다.
 * 
 * ORCH-014: main.ts Mixed Responsibilities → 윈도우 관리 모듈 분리
 */

import { BrowserWindow, session } from 'electron';
import path from 'path';
import {
  WINDOW_DEFAULT_WIDTH,
  WINDOW_DEFAULT_HEIGHT,
  WINDOW_MIN_WIDTH,
  WINDOW_MIN_HEIGHT,
} from '../constants';
import { createLogger } from './logger.service';

const log = createLogger('window');

// ============================================================================
// Types
// ============================================================================

interface DevServerOrigins {
  httpOrigin: string;
  wsOrigin: string;
}

interface WindowConfig {
  devServerUrl?: string;
  preloadPath: string;
  rendererPath: string;
}

// ============================================================================
// Window Service
// ============================================================================

/**
 * 윈도우 관리 서비스
 */
class WindowService {
  private mainWindow: BrowserWindow | null = null;

  /**
   * 현재 메인 윈도우를 반환합니다.
   */
  getMainWindow(): BrowserWindow | null {
    return this.mainWindow;
  }

  /**
   * 개발 서버 URL에서 origin 정보를 파싱합니다.
   */
  private parseDevServerOrigins(devServerUrl: string | undefined): DevServerOrigins | null {
    if (!devServerUrl) return null;
    try {
      const url = new URL(devServerUrl);
      return {
        httpOrigin: url.origin,
        wsOrigin: `${url.protocol === 'https:' ? 'wss:' : 'ws:'}//${url.host}`,
      };
    } catch {
      log.error({ url: devServerUrl }, 'Failed to parse dev server URL');
      return null;
    }
  }

  /**
   * Content Security Policy를 설정합니다.
   */
  private configureContentSecurityPolicy(origins: DevServerOrigins | null): void {
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      const responseHeaders = details.responseHeaders ?? {};
      const csp = [
        "default-src 'self'",
        origins
          ? `script-src 'self' ${origins.httpOrigin} 'unsafe-inline'`
          : "script-src 'self' file://",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
        "img-src 'self' data: blob:",
        origins
          ? `connect-src 'self' ${origins.httpOrigin} ${origins.wsOrigin}`
          : "connect-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
      ].join('; ');

      responseHeaders['Content-Security-Policy'] = [csp];
      callback({ responseHeaders });
    });
  }

  /**
   * 메인 윈도우를 생성합니다.
   */
  createWindow(config: WindowConfig): BrowserWindow {
    const devOrigins = this.parseDevServerOrigins(config.devServerUrl);
    this.configureContentSecurityPolicy(devOrigins);

    this.mainWindow = new BrowserWindow({
      width: WINDOW_DEFAULT_WIDTH,
      height: WINDOW_DEFAULT_HEIGHT,
      minWidth: WINDOW_MIN_WIDTH,
      minHeight: WINDOW_MIN_HEIGHT,
      webPreferences: {
        preload: config.preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
      },
      titleBarStyle: 'hiddenInset',
      show: false,
    });

    this.mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
      log.error({ errorCode, errorDescription, validatedURL }, 'Renderer failed to load');
    });

    this.mainWindow.webContents.on('did-finish-load', () => {
      log.info('Renderer loaded successfully');
    });

    this.mainWindow.once('ready-to-show', () => {
      this.mainWindow?.show();
      if (config.devServerUrl) {
        this.mainWindow?.webContents.openDevTools();
      }
    });

    if (config.devServerUrl) {
      log.info({ url: config.devServerUrl }, 'Loading dev server URL');
      this.mainWindow.loadURL(config.devServerUrl);
    } else {
      log.info({ path: config.rendererPath }, 'Loading renderer file');
      this.mainWindow.loadFile(config.rendererPath);
    }

    return this.mainWindow;
  }

  /**
   * 활성 윈도우가 없을 때 새 윈도우를 생성합니다.
   */
  ensureWindow(config: WindowConfig): BrowserWindow {
    if (BrowserWindow.getAllWindows().length === 0) {
      return this.createWindow(config);
    }
    return this.mainWindow!;
  }

  /**
   * 메인 윈도우 참조를 정리합니다.
   */
  clearWindow(): void {
    this.mainWindow = null;
  }
}

// 싱글톤 인스턴스 export
export const windowService = new WindowService();
