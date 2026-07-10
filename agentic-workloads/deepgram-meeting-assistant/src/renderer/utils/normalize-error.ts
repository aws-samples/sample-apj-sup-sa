export const normalizeErrorMessage = (value: unknown, fallback = '알 수 없는 오류'): string => {
  if (!value) {
    return fallback;
  }

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed) as { message?: unknown; Message?: unknown };
        const message = parsed.message ?? parsed.Message;
        if (typeof message === 'string' && message.trim()) {
          return message;
        }
      } catch {
        // fall through to raw value
      }
    }
    return value;
  }

  if (value instanceof Error) {
    return value.message || fallback;
  }

  if (typeof value === 'object') {
    const maybeMessage = (value as { message?: unknown }).message;
    if (typeof maybeMessage === 'string' && maybeMessage.trim()) {
      return maybeMessage;
    }

    try {
      const serialized = JSON.stringify(value);
      return serialized === '{}' ? fallback : serialized;
    } catch {
      return fallback;
    }
  }

  return String(value);
};
