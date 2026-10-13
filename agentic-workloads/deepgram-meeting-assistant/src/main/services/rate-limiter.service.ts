/**
 * Rate Limiter Service
 * 
 * 비용이 큰 API 호출(요약 생성, AI 제안 등)에 대한 Rate Limiting을 제공합니다.
 * 
 * ORCH-025: No Rate Limiting → 호출 빈도 제한 도입
 */

import { createLogger } from './logger.service';

const log = createLogger('rate-limiter');

interface RateLimitConfig {
  /** 윈도우 크기 (밀리초) */
  windowMs: number;
  /** 윈도우 내 최대 요청 수 */
  maxRequests: number;
}

interface RateLimitState {
  /** 요청 타임스탬프 배열 */
  timestamps: number[];
}

/**
 * Rate Limiter 클래스
 * 슬라이딩 윈도우 방식으로 요청 빈도를 제한합니다.
 */
class RateLimiter {
  private limits: Map<string, RateLimitState> = new Map();
  private configs: Map<string, RateLimitConfig> = new Map();

  /**
   * Rate limit 설정을 등록합니다.
   */
  register(key: string, config: RateLimitConfig): void {
    this.configs.set(key, config);
    this.limits.set(key, { timestamps: [] });
    log.debug({ key, windowMs: config.windowMs, maxRequests: config.maxRequests }, 'Rate limit registered');
  }

  /**
   * 요청이 허용되는지 확인합니다.
   * @returns true면 허용, false면 제한됨
   */
  isAllowed(key: string): boolean {
    const config = this.configs.get(key);
    const state = this.limits.get(key);

    if (!config || !state) {
      log.warn({ key }, 'Rate limit not configured for key');
      return true; // 설정되지 않은 키는 허용
    }

    const now = Date.now();
    const windowStart = now - config.windowMs;

    // 윈도우 밖의 오래된 타임스탬프 제거
    state.timestamps = state.timestamps.filter((ts) => ts > windowStart);

    if (state.timestamps.length >= config.maxRequests) {
      const oldestTs = state.timestamps[0];
      const retryAfterMs = oldestTs + config.windowMs - now;
      log.warn(
        { key, currentCount: state.timestamps.length, maxRequests: config.maxRequests, retryAfterMs },
        'Rate limit exceeded'
      );
      return false;
    }

    return true;
  }

  /**
   * 요청을 기록합니다.
   */
  record(key: string): void {
    const state = this.limits.get(key);
    if (state) {
      state.timestamps.push(Date.now());
    }
  }

  /**
   * 요청을 시도합니다. 허용되면 기록하고 true 반환, 제한되면 false 반환.
   */
  tryRequest(key: string): boolean {
    if (!this.isAllowed(key)) {
      return false;
    }
    this.record(key);
    return true;
  }

  /**
   * 다음 요청까지 대기해야 하는 시간(밀리초)을 반환합니다.
   * 즉시 요청 가능하면 0을 반환합니다.
   */
  getRetryAfterMs(key: string): number {
    const config = this.configs.get(key);
    const state = this.limits.get(key);

    if (!config || !state || state.timestamps.length < config.maxRequests) {
      return 0;
    }

    const now = Date.now();
    const windowStart = now - config.windowMs;
    const validTimestamps = state.timestamps.filter((ts) => ts > windowStart);

    if (validTimestamps.length < config.maxRequests) {
      return 0;
    }

    const oldestTs = validTimestamps[0];
    return Math.max(0, oldestTs + config.windowMs - now);
  }

  /**
   * 특정 키의 현재 요청 수를 반환합니다.
   */
  getCurrentCount(key: string): number {
    const config = this.configs.get(key);
    const state = this.limits.get(key);

    if (!config || !state) {
      return 0;
    }

    const now = Date.now();
    const windowStart = now - config.windowMs;
    return state.timestamps.filter((ts) => ts > windowStart).length;
  }

  /**
   * 특정 키의 상태를 초기화합니다.
   */
  reset(key: string): void {
    const state = this.limits.get(key);
    if (state) {
      state.timestamps = [];
      log.debug({ key }, 'Rate limit reset');
    }
  }
}

// 싱글톤 인스턴스
export const rateLimiter = new RateLimiter();

// 기본 Rate Limit 키 상수
export const RATE_LIMIT_KEYS = {
  SUMMARY_GENERATION: 'summary:generate',
  CONVERSATION_LOG_GENERATION: 'conversation-log:generate',
  ENGLISH_SUGGESTIONS: 'english:suggestions',
  TRANSLATION: 'english:translate',
} as const;

// 기본 설정 등록
// 요약 생성: 1분에 3회 제한 (비용이 가장 큼)
rateLimiter.register(RATE_LIMIT_KEYS.SUMMARY_GENERATION, {
  windowMs: 60 * 1000, // 1분
  maxRequests: 3,
});

// 대화 로그 생성: 1분에 3회 제한 (요약과 동일)
rateLimiter.register(RATE_LIMIT_KEYS.CONVERSATION_LOG_GENERATION, {
  windowMs: 60 * 1000,
  maxRequests: 3,
});

// 영어 제안: 1분에 10회 제한
rateLimiter.register(RATE_LIMIT_KEYS.ENGLISH_SUGGESTIONS, {
  windowMs: 60 * 1000,
  maxRequests: 10,
});

// 번역: 1분에 20회 제한
rateLimiter.register(RATE_LIMIT_KEYS.TRANSLATION, {
  windowMs: 60 * 1000,
  maxRequests: 20,
});
