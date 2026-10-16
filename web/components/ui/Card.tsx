import type { ComponentPropsWithoutRef } from "react";

/** Use direct children as sections; separators keep a single border level. */
export default function Card({ children, className = "", ...props }: ComponentPropsWithoutRef<"div">) {
  return <div {...props} className={`card min-w-0 divide-y divide-line ${className}`}>{children}</div>;
}
