import React from 'react';

interface ErrorBannerProps {
  message: string;
  onClear: () => void;
}

const ErrorBanner: React.FC<ErrorBannerProps> = ({ message, onClear }) => {
  if (!message) return null;

  return (
    <div className="error-banner" role="alert">
      <div className="error-banner-content">
        <div className="error-icon-wrapper">
          <span className="material-symbols-outlined error-icon">error</span>
        </div>
        <div className="error-message-container">
          <span className="error-message">{message}</span>
        </div>
        <button
          type="button"
          className="error-close-btn"
          onClick={onClear}
          aria-label="Dismiss error"
        >
          <span className="material-symbols-outlined">close</span>
        </button>
      </div>
    </div>
  );
};

export default ErrorBanner;
