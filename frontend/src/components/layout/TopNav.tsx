import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "rounded-md px-3 py-2 text-sm font-medium transition-colors",
          isActive ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
        )
      }
    >
      {label}
    </NavLink>
  );
}

export function TopNav() {
  return (
    <header className="border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <div className="text-sm font-semibold">EC Predict Flow</div>
          <nav className="flex items-center gap-1">
            <NavItem to="/" label="一键运行" />
            <NavItem to="/templates" label="模板" />
          </nav>
        </div>
        <div className="text-xs text-muted-foreground">v2</div>
      </div>
    </header>
  );
}

