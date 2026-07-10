import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import micIcon from '../assets/images/microphone.png';

const DROPDOWN_GAP_PX = 8;
const STORED_DEVICE_ID_KEY = 'meeting-assistant:audio-input-device-id';

interface AudioInputDevice {
  deviceId: string;
  label: string;
  kind: MediaDeviceKind;
}

interface MicrophoneControlProps {
  onDeviceChange?: (deviceId: string | null) => void;
  isMuted?: boolean;
  onToggleMute?: () => void;
}

function MicrophoneControl({ onDeviceChange, isMuted = false, onToggleMute }: MicrophoneControlProps) {
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');
  const [selectedDeviceLabel, setSelectedDeviceLabel] = useState<string>('');
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const selectedDeviceIdRef = useRef('');

  useEffect(() => {
    selectedDeviceIdRef.current = selectedDeviceId;
  }, [selectedDeviceId]);

  const getStoredDeviceId = () => {
    try {
      return localStorage.getItem(STORED_DEVICE_ID_KEY);
    } catch {
      return null;
    }
  };

  const setStoredDeviceId = (deviceId: string) => {
    try {
      localStorage.setItem(STORED_DEVICE_ID_KEY, deviceId);
    } catch {
      // Ignore storage failures (e.g., privacy mode).
    }
  };

  useEffect(() => {
    const loadDevices = async () => {
      try {
        const deviceList = await navigator.mediaDevices.enumerateDevices();
        const audioInputs = deviceList
          .filter(device => device.kind === 'audioinput')
          .map((device, index) => ({
            deviceId: device.deviceId,
            label: device.label || `마이크 ${index + 1}`,
            kind: device.kind,
          }));

        setDevices(audioInputs);

        if (audioInputs.length === 0) {
          setSelectedDeviceId('');
          setSelectedDeviceLabel('');
          onDeviceChange?.(null);
          return;
        }

        const storedDeviceId = getStoredDeviceId();
        const preferredDeviceId = storedDeviceId || selectedDeviceIdRef.current;
        const preferredDevice = preferredDeviceId
          ? audioInputs.find((device) => device.deviceId === preferredDeviceId)
          : null;

        const nextDevice = preferredDevice ?? audioInputs[0];
        setSelectedDeviceId(nextDevice.deviceId);
        setSelectedDeviceLabel(nextDevice.label);
        setStoredDeviceId(nextDevice.deviceId);

        if (nextDevice.deviceId !== selectedDeviceIdRef.current) {
          onDeviceChange?.(nextDevice.deviceId);
        }
      } catch (error) {
        console.error('마이크 목록 로드 실패:', error);
        setDevices([]);
      }
    };

    loadDevices();

    const handleDeviceChange = () => {
      loadDevices();
    };

    navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange);

    return () => {
      navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange);
    };
  }, []);

  useEffect(() => {
    if (isDropdownOpen && dropdownRef.current && wrapperRef.current) {
      const wrapperRect = wrapperRef.current.getBoundingClientRect();
      const dropdown = dropdownRef.current;
      
      dropdown.style.bottom = `${window.innerHeight - wrapperRect.top + DROPDOWN_GAP_PX}px`;
      dropdown.style.left = `${wrapperRect.left}px`;
      dropdown.style.top = 'auto';
    }
  }, [isDropdownOpen]);

  const handleDeviceSelect = (device: AudioInputDevice) => {
    setSelectedDeviceId(device.deviceId);
    setSelectedDeviceLabel(device.label);
    setIsDropdownOpen(false);
    setStoredDeviceId(device.deviceId);
    onDeviceChange?.(device.deviceId);
  };

  return (
    <div className="microphone-control">
      <div ref={wrapperRef} className="mic-control-wrapper">
        <button
          className={`mic-button ${isMuted ? 'muted' : ''}`}
          onClick={onToggleMute}
          type="button"
          aria-pressed={isMuted}
          title={isMuted ? '마이크 켜기' : '마이크 끄기'}
          disabled={!onToggleMute}
        >
          <span className="mic-icon-wrapper">
            <img 
              src={micIcon} 
              alt="마이크" 
              className="mic-icon"
            />
            {isMuted && <span className="mic-mute-overlay" />}
          </span>
        </button>
        
        <div className="mic-dropdown-container">
          <div className="mic-device-selector">
            <span className="mic-device-label">{selectedDeviceLabel}</span>
            <button
              className={`mic-dropdown-trigger ${isDropdownOpen ? 'open' : ''}`}
              onClick={(e) => {
                e.stopPropagation();
                setIsDropdownOpen((prev) => !prev);
              }}
              title="마이크 디바이스 선택"
              aria-label="마이크 디바이스 선택"
              type="button"
            >
              <span className={`material-symbols-outlined ${isDropdownOpen ? 'rotated' : ''}`}>
                expand_more
              </span>
            </button>
          </div>
          
          {isDropdownOpen && createPortal(
            <>
              <div
                className="mic-dropdown-overlay"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsDropdownOpen(false);
                }}
              />
              <div ref={dropdownRef} className="mic-dropdown-menu">
                {devices.length === 0 ? (
                  <div className="mic-dropdown-item empty">
                    사용 가능한 마이크가 없습니다
                  </div>
                ) : (
                  devices.map((device) => (
                    <button
                      key={device.deviceId}
                      className={`mic-dropdown-item ${device.deviceId === selectedDeviceId ? 'selected' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeviceSelect(device);
                      }}
                      type="button"
                    >
                      <span className="material-symbols-outlined">
                        {device.deviceId === selectedDeviceId ? 'check' : 'radio_button_unchecked'}
                      </span>
                      <span>{device.label}</span>
                    </button>
                  ))
                )}
              </div>
            </>,
            document.body
          )}
        </div>
      </div>
    </div>
  );
}

export default MicrophoneControl;
