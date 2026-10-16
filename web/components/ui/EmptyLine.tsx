import type { ReactNode } from "react";

export default function EmptyLine({ children }: { children: ReactNode }) {
  return <p className="py-3 text-sm text-dim">{children}</p>;
}
