import pino from 'pino';
import pinoPretty from 'pino-pretty';
import { app } from 'electron';
import path from 'path';
import fs from 'fs';

// `app` is undefined when this module is loaded outside the Electron main
// process (e.g. in unit tests that import a service without mocking electron).
// Fall back to dev defaults so logging never crashes module load.
const isDev = app ? !app.isPackaged : true;
const appVersion = app ? app.getVersion() : '0.0.0-dev';

const baseOptions = {
  level: isDev ? 'debug' : 'info',
  base: {
    app: 'meeting-assistant',
    version: appVersion,
  },
};

function createLogStream() {
  if (isDev) {
    return pinoPretty({
      colorize: true,
      translateTime: 'SYS:standard',
      ignore: 'pid,hostname',
    });
  }

  const logDir = path.join(app.getPath('userData'), 'logs');
  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir, { recursive: true });
  }

  const logFile = path.join(logDir, `app-${Date.now()}.log`);
  const fileStream = fs.createWriteStream(logFile, { flags: 'a' });

  const prettyStream = pinoPretty({
    colorize: false,
    translateTime: 'SYS:standard',
    ignore: 'pid,hostname',
    destination: fileStream,
  });

  return prettyStream;
}

const logStream = createLogStream();
export const logger = pino(baseOptions, logStream);

/** 서비스별 child logger 생성 */
export function createLogger(service: string) {
  return logger.child({ service });
}
