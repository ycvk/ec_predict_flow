import { Outlet } from "react-router-dom";

import { TopNav } from "@/components/layout/TopNav";

export function AppLayout() {
  return (
    <div className="min-h-dvh bg-background">
      <TopNav />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}

