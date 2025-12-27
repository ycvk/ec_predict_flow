import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/layout/AppLayout";
import { HomePage } from "@/pages/HomePage";
import { RunDetailPage } from "@/pages/RunDetailPage";
import { TemplatesPage } from "@/pages/TemplatesPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="templates" element={<TemplatesPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

