export const isElectron = (): boolean => typeof window.electronAPI !== 'undefined';

export const getElectronAPI = () => (isElectron() ? window.electronAPI : null);
