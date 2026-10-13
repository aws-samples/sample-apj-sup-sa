import { TranscribeService, type TranscribeServiceConfig } from '../services/transcribe.service';
import { BedrockService, type BedrockServiceConfig } from '../services/bedrock.service';
import { SentenceBufferService } from '../services/sentence-buffer.service';
import type { TranscribeLanguage } from '../../shared/types/settings';

export class ServiceFactory {
  static createTranscribeService(config: TranscribeServiceConfig): TranscribeService {
    return new TranscribeService(config);
  }

  static createBedrockService(config: BedrockServiceConfig): BedrockService {
    return new BedrockService(config);
  }

  static createSentenceBufferService(language: TranscribeLanguage): SentenceBufferService {
    return new SentenceBufferService(language);
  }
}
