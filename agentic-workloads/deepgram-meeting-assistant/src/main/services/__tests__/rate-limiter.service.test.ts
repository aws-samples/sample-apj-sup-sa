/**
 * RateLimiter Service Tests
 * 
 * Kent Beck 스타일:
 * - 슬라이딩 윈도우 알고리즘의 정확성 검증
 * - 시간에 의존하는 로직을 가상 시간으로 테스트
 * - 경계 조건(윈도우 전환 시점 등) 집중 테스트
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { rateLimiter } from '../rate-limiter.service';

// Logger mock
vi.mock('../logger.service', () => ({
  createLogger: vi.fn(() => ({
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  })),
}));

describe('RateLimiter', () => {
  const TEST_KEY = 'test:key';
  const WINDOW_MS = 1000; // 1초
  const MAX_REQUESTS = 2;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 0, 1, 12, 0, 0)); // 고정된 시작 시간
    rateLimiter.register(TEST_KEY, { windowMs: WINDOW_MS, maxRequests: MAX_REQUESTS });
  });

  afterEach(() => {
    rateLimiter.reset(TEST_KEY);
    vi.useRealTimers();
  });

  describe('tryRequest', () => {
    it('허용된 요청 횟수 내에서는 true를 반환하고 기록한다', () => {
      expect(rateLimiter.tryRequest(TEST_KEY)).toBe(true);
      expect(rateLimiter.tryRequest(TEST_KEY)).toBe(true);
      expect(rateLimiter.getCurrentCount(TEST_KEY)).toBe(2);
    });

    it('허용 횟수를 초과하면 false를 반환하고 기록하지 않는다', () => {
      rateLimiter.tryRequest(TEST_KEY);
      rateLimiter.tryRequest(TEST_KEY);
      
      expect(rateLimiter.tryRequest(TEST_KEY)).toBe(false);
      expect(rateLimiter.getCurrentCount(TEST_KEY)).toBe(2);
    });

    it('슬라이딩 윈도우: 시간이 지나면 다시 허용된다', () => {
      rateLimiter.tryRequest(TEST_KEY); // t=0
      
      vi.advanceTimersByTime(600);
      rateLimiter.tryRequest(TEST_KEY); // t=600
      
      expect(rateLimiter.tryRequest(TEST_KEY)).toBe(false); // 횟수 초과

      // 첫 번째 요청이 윈도우 밖으로 나가는 시점 (t=1001)
      vi.advanceTimersByTime(401);
      expect(rateLimiter.tryRequest(TEST_KEY)).toBe(true);
    });
  });

  describe('getRetryAfterMs', () => {
    it('요청이 가능할 때는 0을 반환한다', () => {
      expect(rateLimiter.getRetryAfterMs(TEST_KEY)).toBe(0);
    });

    it('제한되었을 때 다음 요청 가능 시점까지의 남은 시간을 반환한다', () => {
      const startTime = Date.now();
      rateLimiter.tryRequest(TEST_KEY); // t=0
      
      vi.advanceTimersByTime(200);
      rateLimiter.tryRequest(TEST_KEY); // t=200
      
      // 제한됨. 첫 번째 요청(t=0)이 만료되는 t=1000까지 800ms 남음
      expect(rateLimiter.getRetryAfterMs(TEST_KEY)).toBe(800);
      
      vi.advanceTimersByTime(300); // t=500
      expect(rateLimiter.getRetryAfterMs(TEST_KEY)).toBe(500);
    });
  });

  describe('reset', () => {
    it('기록된 타임스탬프를 모두 초기화한다', () => {
      rateLimiter.tryRequest(TEST_KEY);
      rateLimiter.tryRequest(TEST_KEY);
      expect(rateLimiter.isAllowed(TEST_KEY)).toBe(false);

      rateLimiter.reset(TEST_KEY);
      expect(rateLimiter.isAllowed(TEST_KEY)).toBe(true);
      expect(rateLimiter.getCurrentCount(TEST_KEY)).toBe(0);
    });
  });
});
