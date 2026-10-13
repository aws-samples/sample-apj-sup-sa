import type { LeadershipPrinciple, LPInfo, InterviewQuestion } from '../types/interview';

export const LEADERSHIP_PRINCIPLES: LPInfo[] = [
  { id: 'customer-obsession', name: 'Customer Obsession', shortName: 'Customer' },
  { id: 'ownership', name: 'Ownership', shortName: 'Ownership' },
  { id: 'invent-and-simplify', name: 'Invent and Simplify', shortName: 'Invent' },
  { id: 'learn-and-be-curious', name: 'Learn and Be Curious', shortName: 'Learn' },
  { id: 'insist-on-highest-standards', name: 'Insist on the Highest Standards', shortName: 'Standards' },
  { id: 'bias-for-action', name: 'Bias for Action', shortName: 'Action' },
  { id: 'earn-trust', name: 'Earn Trust', shortName: 'Trust' },
  { id: 'dive-deep', name: 'Dive Deep', shortName: 'Dive Deep' },
  { id: 'have-backbone', name: 'Have Backbone; Disagree and Commit', shortName: 'Backbone' },
  { id: 'deliver-results', name: 'Deliver Results', shortName: 'Results' },
];

export const LP_QUESTIONS: Record<LeadershipPrinciple, InterviewQuestion[]> = {
  'customer-obsession': [
    { id: 'co-1', lpId: 'customer-obsession', text: 'Tell me about a time when you went above and beyond for a customer.' },
    { id: 'co-2', lpId: 'customer-obsession', text: 'Describe a situation where you had to balance customer needs with business constraints.' },
    { id: 'co-3', lpId: 'customer-obsession', text: 'How do you gather and incorporate customer feedback into your work?' },
    { id: 'co-4', lpId: 'customer-obsession', text: 'Tell me about a time you anticipated a customer need before they expressed it.' },
    { id: 'co-5', lpId: 'customer-obsession', text: 'Describe a difficult customer situation and how you handled it.' },
  ],
  'ownership': [
    { id: 'ow-1', lpId: 'ownership', text: 'Tell me about a time you took ownership of a project outside your normal responsibilities.' },
    { id: 'ow-2', lpId: 'ownership', text: 'Describe a situation where you identified a problem and fixed it without being asked.' },
    { id: 'ow-3', lpId: 'ownership', text: 'How do you prioritize long-term value over short-term results?' },
    { id: 'ow-4', lpId: 'ownership', text: 'Tell me about a time you had to make a decision that affected the whole team or company.' },
    { id: 'ow-5', lpId: 'ownership', text: 'Describe a project where you took end-to-end ownership.' },
  ],
  'invent-and-simplify': [
    { id: 'is-1', lpId: 'invent-and-simplify', text: 'Tell me about a time you invented something new or simplified a complex process.' },
    { id: 'is-2', lpId: 'invent-and-simplify', text: 'Describe a situation where you challenged the status quo.' },
    { id: 'is-3', lpId: 'invent-and-simplify', text: 'How do you approach finding innovative solutions to problems?' },
    { id: 'is-4', lpId: 'invent-and-simplify', text: 'Tell me about a time you reduced complexity in a system or process.' },
    { id: 'is-5', lpId: 'invent-and-simplify', text: 'Describe an idea you proposed that was initially misunderstood.' },
  ],
  'learn-and-be-curious': [
    { id: 'lc-1', lpId: 'learn-and-be-curious', text: 'Tell me about a time you learned something new that helped you in your role.' },
    { id: 'lc-2', lpId: 'learn-and-be-curious', text: 'How do you stay current with industry trends and technologies?' },
    { id: 'lc-3', lpId: 'learn-and-be-curious', text: 'Describe a situation where your curiosity led to a better outcome.' },
    { id: 'lc-4', lpId: 'learn-and-be-curious', text: 'Tell me about a skill you developed outside your comfort zone.' },
    { id: 'lc-5', lpId: 'learn-and-be-curious', text: 'How do you approach learning from failures?' },
  ],
  'insist-on-highest-standards': [
    { id: 'hs-1', lpId: 'insist-on-highest-standards', text: 'Tell me about a time you refused to compromise on quality.' },
    { id: 'hs-2', lpId: 'insist-on-highest-standards', text: 'Describe a situation where you raised the bar for your team.' },
    { id: 'hs-3', lpId: 'insist-on-highest-standards', text: 'How do you ensure quality in your deliverables?' },
    { id: 'hs-4', lpId: 'insist-on-highest-standards', text: 'Tell me about a time you identified a defect others missed.' },
    { id: 'hs-5', lpId: 'insist-on-highest-standards', text: 'Describe how you handle situations where standards are not being met.' },
  ],
  'bias-for-action': [
    { id: 'ba-1', lpId: 'bias-for-action', text: 'Tell me about a time you made a decision with incomplete information.' },
    { id: 'ba-2', lpId: 'bias-for-action', text: 'Describe a situation where speed was critical to success.' },
    { id: 'ba-3', lpId: 'bias-for-action', text: 'How do you balance analysis with taking action?' },
    { id: 'ba-4', lpId: 'bias-for-action', text: 'Tell me about a calculated risk you took.' },
    { id: 'ba-5', lpId: 'bias-for-action', text: 'Describe a time when waiting would have been costly.' },
  ],
  'earn-trust': [
    { id: 'et-1', lpId: 'earn-trust', text: 'Tell me about a time you had to rebuild trust with a colleague or customer.' },
    { id: 'et-2', lpId: 'earn-trust', text: 'Describe a situation where you gave difficult feedback.' },
    { id: 'et-3', lpId: 'earn-trust', text: 'How do you build trust with new team members?' },
    { id: 'et-4', lpId: 'earn-trust', text: 'Tell me about a time you admitted a mistake.' },
    { id: 'et-5', lpId: 'earn-trust', text: 'Describe how you handle confidential information.' },
  ],
  'dive-deep': [
    { id: 'dd-1', lpId: 'dive-deep', text: 'Tell me about a time you had to dig deep to find the root cause of a problem.' },
    { id: 'dd-2', lpId: 'dive-deep', text: 'Describe a situation where details made a significant difference.' },
    { id: 'dd-3', lpId: 'dive-deep', text: 'How do you stay connected to the details while managing big picture?' },
    { id: 'dd-4', lpId: 'dive-deep', text: 'Tell me about a time you discovered something important by asking questions.' },
    { id: 'dd-5', lpId: 'dive-deep', text: 'Describe how you verify information before making decisions.' },
  ],
  'have-backbone': [
    { id: 'hb-1', lpId: 'have-backbone', text: 'Tell me about a time you disagreed with your manager or team.' },
    { id: 'hb-2', lpId: 'have-backbone', text: 'Describe a situation where you stood firm on a decision despite pushback.' },
    { id: 'hb-3', lpId: 'have-backbone', text: 'How do you handle situations where you disagree with the majority?' },
    { id: 'hb-4', lpId: 'have-backbone', text: 'Tell me about a time you committed to a decision you initially disagreed with.' },
    { id: 'hb-5', lpId: 'have-backbone', text: 'Describe how you voice concerns in a constructive way.' },
  ],
  'deliver-results': [
    { id: 'dr-1', lpId: 'deliver-results', text: 'Tell me about a time you delivered results under tight deadlines.' },
    { id: 'dr-2', lpId: 'deliver-results', text: 'Describe a situation where you overcame obstacles to achieve a goal.' },
    { id: 'dr-3', lpId: 'deliver-results', text: 'How do you prioritize when everything seems urgent?' },
    { id: 'dr-4', lpId: 'deliver-results', text: 'Tell me about a project where you exceeded expectations.' },
    { id: 'dr-5', lpId: 'deliver-results', text: 'Describe how you handle setbacks while working toward a goal.' },
  ],
};

export function getQuestionsForLPs(lpIds: LeadershipPrinciple[]): InterviewQuestion[] {
  return lpIds.flatMap((lpId) => LP_QUESTIONS[lpId] || []);
}

export function getLPInfo(lpId: LeadershipPrinciple): LPInfo | undefined {
  return LEADERSHIP_PRINCIPLES.find((lp) => lp.id === lpId);
}
