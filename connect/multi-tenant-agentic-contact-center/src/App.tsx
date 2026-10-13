import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { RequireAuth } from "@/components/RequireAuth";
import PersonaSelect from "@/pages/PersonaSelect";
import AuthCallback from "@/pages/AuthCallback";

import Docs from "@/pages/Docs";
import MerchantTransactions from "@/pages/merchant/Transactions";
import MerchantSupport from "@/pages/merchant/Support";

import AdminCases from "@/pages/admin/CaseManagement";

// Lazy-loaded so amazon-connect-streams (which ContactCenter imports and which
// attaches to the shared window.connect global) is code-split into its own
// chunk and only loads on /admin/contact-center — never on merchant pages,
// where it would clash with ChatJS ("There is no upstream conduit!").
const AdminContactCenter = lazy(() => import("@/pages/admin/ContactCenter"));

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PersonaSelect />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      {/* Public developer docs — reachable with or without login, by anyone. */}
      <Route path="/docs" element={<Docs />} />

      <Route
        path="/merchant"
        element={
          <RequireAuth persona="merchant">
            <DashboardLayout persona="merchant" />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="transactions" replace />} />
        <Route path="transactions" element={<MerchantTransactions />} />
        <Route path="support" element={<MerchantSupport />} />
      </Route>

      <Route
        path="/admin"
        element={
          <RequireAuth persona="admin">
            <DashboardLayout persona="admin" />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="cases" replace />} />
        <Route path="cases" element={<AdminCases />} />
        <Route
          path="contact-center"
          element={
            <Suspense fallback={<div className="p-6 text-sm text-ink-400">Loading contact center…</div>}>
              <AdminContactCenter />
            </Suspense>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
