export type Persona = "merchant" | "admin";

export type PaymentStatus =
  | "succeeded"
  | "pending"
  | "failed"
  | "refunded"
  | "disputed";

export type PaymentMethod =
  | "card"
  | "gcash"
  | "grab_pay"
  | "bank_transfer"
  | "apple_pay";

export interface Payment {
  id: string;
  merchantId: string;
  amount: number; // in cents
  currency: string;
  status: PaymentStatus;
  method: PaymentMethod;
  customerName: string;
  customerEmail: string;
  description: string;
  createdAt: string; // ISO
  fee: number; // in cents
  riskScore: number; // 0-100
}

export type PayoutStatus = "paid" | "in_transit" | "scheduled" | "failed";

export interface Payout {
  id: string;
  merchantId: string;
  amount: number;
  currency: string;
  status: PayoutStatus;
  bankLast4: string;
  arrivalDate: string;
  createdAt: string;
}

export interface Customer {
  id: string;
  merchantId: string;
  name: string;
  email: string;
  totalSpent: number;
  paymentsCount: number;
  createdAt: string;
  defaultMethod: PaymentMethod;
}

export type MerchantStatus = "active" | "review" | "restricted" | "onboarding";

export interface Merchant {
  id: string;
  name: string;
  legalName: string;
  email: string;
  status: MerchantStatus;
  country: string;
  category: string;
  volume30d: number; // cents
  successRate: number; // 0-100
  disputeRate: number; // 0-100
  riskLevel: "low" | "medium" | "high";
  createdAt: string;
  logoColor: string;
}

export type DisputeStatus =
  | "needs_response"
  | "under_review"
  | "won"
  | "lost";

export interface Dispute {
  id: string;
  paymentId: string;
  merchantId: string;
  merchantName: string;
  amount: number;
  currency: string;
  reason: string;
  status: DisputeStatus;
  openedAt: string;
  dueBy: string;
  customerName: string;
}

export type TicketPriority = "low" | "medium" | "high" | "urgent";
export type TicketStatus = "open" | "pending" | "resolved";

export interface SupportTicket {
  id: string;
  subject: string;
  merchantId: string;
  merchantName: string;
  priority: TicketPriority;
  status: TicketStatus;
  assignedAgent: string | null;
  createdAt: string;
  updatedAt: string;
  category: string;
  messages: number;
}

export interface TimePoint {
  label: string;
  value: number;
  secondary?: number;
}
