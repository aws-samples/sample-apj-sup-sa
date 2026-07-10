export interface TranslatedSuggestionItem {
  text: string;
  translatedText: string;
}

export interface TranslatedSuggestionResult {
  suggestions: TranslatedSuggestionItem[];
}

/** @deprecated Use TranslatedSuggestionItem instead */
export type EnglishSuggestionItem = TranslatedSuggestionItem;

/** @deprecated Use TranslatedSuggestionResult instead */
export type EnglishSuggestionResult = TranslatedSuggestionResult;
