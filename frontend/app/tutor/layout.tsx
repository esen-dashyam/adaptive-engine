import type { ReactNode } from "react";

export default function TutorLayout({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-6 -my-8 flex-1 flex flex-col overflow-hidden">
      {children}
    </div>
  );
}
