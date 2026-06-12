export type LeadershipPrinciple =
  | 'customer-obsession'
  | 'ownership'
  | 'invent-and-simplify'
  | 'learn-and-be-curious'
  | 'insist-on-highest-standards'
  | 'bias-for-action'
  | 'earn-trust'
  | 'dive-deep'
  | 'have-backbone'
  | 'deliver-results';

export interface LPInfo {
  id: LeadershipPrinciple;
  name: string;
  shortName: string;
}

export interface InterviewQuestion {
  id: string;
  lpId: LeadershipPrinciple;
  text: string;
}

export interface InterviewSuggestionItem {
  text: string;
  lpId: LeadershipPrinciple;
  lpName: string;
}

export interface InterviewSuggestionResult {
  suggestions: InterviewSuggestionItem[];
}
