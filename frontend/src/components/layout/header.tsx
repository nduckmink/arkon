"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

type NavItem = {
  label: string;
  href: string;
  icon: string;
  adminOnly?: boolean;
};

const navItems: NavItem[] = [
  { label: "Dashboard", href: "/", icon: "dashboard" },
  { label: "Knowledge Base", href: "/knowledge", icon: "database" },
  { label: "Knowledge Types", href: "/types", icon: "category", adminOnly: true },
  { label: "Projects", href: "/projects", icon: "folder_special" },
  { label: "Departments", href: "/departments", icon: "business", adminOnly: true },
  { label: "Employees", href: "/employees", icon: "group", adminOnly: true },
  { label: "Contacts", href: "/contacts", icon: "contacts" },
  { label: "Search Preview", href: "/search-preview", icon: "manage_search", adminOnly: true },
  { label: "Settings", href: "/settings", icon: "settings", adminOnly: true },
];

export function Header() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isAdmin = user?.role === "admin";
  const filteredItems = navItems.filter((item) => !item.adminOnly || isAdmin);

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <>
      <header className="sticky top-0 z-30 bg-background/95 backdrop-blur-sm border-b border-border h-14 flex items-center justify-between px-6">
        {/* Left — mobile menu button */}
        <div className="flex items-center gap-3">
          <button
            className="material-symbols-outlined text-muted-foreground cursor-pointer hover:text-foreground transition-colors md:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            menu
          </button>
        </div>

        {/* Right */}
        <div className="flex items-center gap-4">
          <span className="text-xs font-medium text-muted-foreground bg-secondary px-2 py-1 rounded-md">
            On-Premise
          </span>

          <DropdownMenu>
            <DropdownMenuTrigger className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-secondary transition-colors cursor-pointer">
              <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xs font-bold">
                {user?.name?.charAt(0).toUpperCase() || "?"}
              </div>
              <span className="text-sm font-medium hidden sm:inline">{user?.name}</span>
              <span className="material-symbols-outlined text-muted-foreground text-base">
                expand_more
              </span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onClick={() => router.push("/profile")}>
                <span className="material-symbols-outlined mr-2 text-base">person</span>
                Profile
              </DropdownMenuItem>
              {isAdmin && (
                <DropdownMenuItem onClick={() => router.push("/settings")}>
                  <span className="material-symbols-outlined mr-2 text-base">settings</span>
                  Settings
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                <span className="material-symbols-outlined mr-2 text-base">logout</span>
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Mobile navigation drawer */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-64 p-0 bg-sidebar">
          <SheetHeader className="px-5 pt-5 pb-2">
            <SheetTitle className="text-xl font-heading text-primary tracking-tight text-left">
              Arkon
            </SheetTitle>
          </SheetHeader>

          <nav className="flex flex-col gap-1 px-3 py-2">
            {filteredItems.map((item) => {
              const isActive =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileOpen(false)}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold"
                      : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground"
                  )}
                >
                  <span className={cn("material-symbols-outlined", isActive && "filled")}>
                    {item.icon}
                  </span>
                  {item.label}
                </Link>
              );
            })}
          </nav>

          {user && (
            <div className="absolute bottom-0 left-0 right-0 px-5 py-4 border-t border-border">
              <div className="flex items-center gap-3 px-3 py-2">
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xs font-bold">
                  {user.name.charAt(0).toUpperCase()}
                </div>
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium text-foreground truncate">{user.name}</span>
                  <span className="text-xs text-muted-foreground capitalize">{user.role}</span>
                </div>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
