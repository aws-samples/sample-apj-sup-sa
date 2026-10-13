import type { ReactNode } from "react";
import type { Persona } from "@/data/types";
import {
  BookIcon,
  CaseIcon,
  HeadsetIcon,
  LifeBuoyIcon,
  SearchIcon,
} from "@/components/ui/icons";

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
  /** Item is only shown when Amazon Connect is configured (connect module deployed). */
  requiresConnect?: boolean;
  /** Item is only shown when the transaction Search API is configured (zero-ETL module deployed). */
  requiresSearch?: boolean;
}

export interface PersonaMeta {
  key: Persona;
  productName: string;
  roleLabel: string;
  basePath: string;
  nav: NavItem[];
}

const iconCls = "h-[18px] w-[18px]";

// Only AWS-backed features are exposed. Merchants search their own transactions
// (OpenSearch) and manage support (Cases + live chat); admins/agents work cases
// and take contacts in the embedded CCP.
export const personaConfig: Record<Persona, PersonaMeta> = {
  merchant: {
    key: "merchant",
    productName: "AnyCompanyPay",
    roleLabel: "Merchant",
    basePath: "/merchant",
    nav: [
      { to: "/merchant/transactions", label: "Transactions", icon: <SearchIcon className={iconCls} />, requiresSearch: true },
      { to: "/merchant/support", label: "Support", icon: <LifeBuoyIcon className={iconCls} />, requiresConnect: true },
      { to: "/docs", label: "Developer docs", icon: <BookIcon className={iconCls} /> },
    ],
  },
  admin: {
    key: "admin",
    productName: "AnyCompanyPay",
    roleLabel: "Operations",
    basePath: "/admin",
    nav: [
      { to: "/admin/cases", label: "Cases", icon: <CaseIcon className={iconCls} />, requiresConnect: true },
      { to: "/admin/contact-center", label: "Contact Center", icon: <HeadsetIcon className={iconCls} />, requiresConnect: true },
      { to: "/docs", label: "Developer docs", icon: <BookIcon className={iconCls} /> },
    ],
  },
};
