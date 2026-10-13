/**
 * useSettings Hook
 * 
 * 앱 설정 로드/저장/삭제를 담당하는 커스텀 훅입니다.
 * 
 * ORCH-018: Generic Error Handling → 사용자 친화적 에러 메시지 추가
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AppSettings, BedrockSettings, DEFAULT_SETTINGS } from '../../shared/types';
import { getElectronAPI, isElectron } from '../utils/electron';

type SaveStatus = 'idle' | 'success' | 'error';

type EditValues = {
  accessKeyId: string;
  secretAccessKey: string;
};

const STATUS_RESET_DELAY = 3000;

/**
 * 에러 메시지를 사용자 친화적 메시지로 변환합니다.
 * ORCH-018: 타입별 에러 메시지 매핑
 */
function formatUserFriendlyError(error: unknown): string {
  const errorStr = String(error);
  
  // 암호화 관련 에러
  if (errorStr.includes('Encryption is not available')) {
    return '시스템 암호화 기능을 사용할 수 없습니다. 자격 증명을 안전하게 저장하려면 시스템 설정을 확인하세요.';
  }
  
  // 검증 에러
  if (errorStr.includes('Invalid settings format') || errorStr.includes('Invalid parameters')) {
    return '입력 형식이 올바르지 않습니다. 모든 필드를 확인해 주세요.';
  }
  
  // 권한 에러
  if (errorStr.includes('permission') || errorStr.includes('EACCES')) {
    return '파일 저장 권한이 없습니다. 앱을 다시 시작하거나 관리자 권한으로 실행해 주세요.';
  }
  
  // 스토리지 에러
  if (errorStr.includes('storage') || errorStr.includes('disk') || errorStr.includes('ENOSPC')) {
    return '저장 공간이 부족합니다. 디스크 공간을 확인해 주세요.';
  }
  
  // AWS 자격 증명 관련
  if (errorStr.includes('credentials') || errorStr.includes('AWS')) {
    return 'AWS 자격 증명 저장에 실패했습니다. 입력 값을 확인해 주세요.';
  }
  
  // 네트워크 에러 (일반적으로 설정 저장에는 해당하지 않지만 확장성을 위해)
  if (errorStr.includes('network') || errorStr.includes('ECONNREFUSED')) {
    return '네트워크 연결에 문제가 있습니다. 연결 상태를 확인해 주세요.';
  }
  
  // 기본 에러 메시지
  if (errorStr.length > 100) {
    return '설정 저장 중 오류가 발생했습니다. 다시 시도해 주세요.';
  }
  
  return errorStr || '알 수 없는 오류가 발생했습니다.';
}

export const useSettings = () => {
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);
  const [editValues, setEditValues] = useState<EditValues>({
    accessKeyId: '',
    secretAccessKey: '',
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const resetStatusLater = useCallback(() => {
    window.setTimeout(() => {
      setSaveStatus('idle');
      setErrorMessage(null);
    }, STATUS_RESET_DELAY);
  }, []);

  const loadSettings = useCallback(async () => {
    const electronAPI = getElectronAPI();
    if (!electronAPI) {
      setIsLoading(false);
      return;
    }

    try {
      const result = await electronAPI.loadSettings();
      if (result.success && result.settings) {
        setSettings(result.settings);
        setEditValues({
          accessKeyId: result.settings.aws.accessKeyId,
          secretAccessKey: result.settings.aws.secretAccessKey,
        });
      }
    } catch (error) {
      console.error('Failed to load settings:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const handleInputChange = useCallback((field: keyof EditValues, value: string) => {
    setEditValues((prev) => ({ ...prev, [field]: value }));
  }, []);

  const handleRegionChange = useCallback((region: string) => {
    setSettings((prev) => ({
      ...prev,
      aws: { ...prev.aws, region },
    }));
  }, []);

  const handleBedrockModelChange = useCallback(
    (field: 'correctionModelId' | 'translationModelId' | 'summaryModelId', value: string) => {
      setSettings((prev) => ({
        ...prev,
        bedrock: { ...prev.bedrock, [field]: value as BedrockSettings['correctionModelId'] },
      }));
    },
    []
  );

  const saveSettings = useCallback(async () => {
    const electronAPI = getElectronAPI();
    if (!electronAPI) {
      setSaveStatus('error');
      resetStatusLater();
      return;
    }

    setIsSaving(true);
    setSaveStatus('idle');

    try {
      const newSettings: AppSettings = {
        ...settings,
        aws: {
          accessKeyId: editValues.accessKeyId,
          secretAccessKey: editValues.secretAccessKey,
          region: settings.aws.region,
        },
      };

      const result = await electronAPI.saveSettings(newSettings);

      if (result.success) {
        setSettings(newSettings);
        setSaveStatus('success');
      } else {
        setSaveStatus('error');
        setErrorMessage(formatUserFriendlyError(result.error));
      }
    } catch (error) {
      console.error('Failed to save settings:', error);
      setSaveStatus('error');
      setErrorMessage(formatUserFriendlyError(error));
    } finally {
      setIsSaving(false);
      resetStatusLater();
    }
  }, [editValues.accessKeyId, editValues.secretAccessKey, resetStatusLater, settings]);

  const clearSettings = useCallback(async () => {
    const electronAPI = getElectronAPI();
    if (!electronAPI) {
      setSettings(DEFAULT_SETTINGS);
      setEditValues({ accessKeyId: '', secretAccessKey: '' });
      return;
    }

    try {
      const result = await electronAPI.clearSettings();
      if (result.success) {
        setSettings(DEFAULT_SETTINGS);
        setEditValues({ accessKeyId: '', secretAccessKey: '' });
        setSaveStatus('success');
        resetStatusLater();
      } else {
        setSaveStatus('error');
        setErrorMessage(formatUserFriendlyError(result.error));
        resetStatusLater();
      }
    } catch (error) {
      console.error('Failed to clear settings:', error);
      setSaveStatus('error');
      setErrorMessage(formatUserFriendlyError(error));
      resetStatusLater();
    }
  }, [resetStatusLater]);

  const isConfigured = useMemo(
    () => !!(editValues.accessKeyId && editValues.secretAccessKey),
    [editValues.accessKeyId, editValues.secretAccessKey]
  );

  return {
    settings,
    editValues,
    isLoading,
    isSaving,
    saveStatus,
    errorMessage,
    isConfigured,
    isElectron: isElectron(),
    handleInputChange,
    handleRegionChange,
    handleBedrockModelChange,
    saveSettings,
    clearSettings,
  };
};
