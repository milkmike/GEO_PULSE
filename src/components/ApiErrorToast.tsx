"use client";

import { useEffect, useState, useCallback } from "react";

interface Toast {
  id: number;
  message: string;
  type: "error" | "warning";
}

let toastId = 0;
const listeners: Set<(toast: Toast) => void> = new Set();

/** Call from anywhere to show an error toast */
export function showApiError(message: string, type: "error" | "warning" = "error") {
  const toast: Toast = { id: ++toastId, message, type };
  listeners.forEach((fn) => fn(toast));
}

export function ApiErrorToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((toast: Toast) => {
    setToasts((prev) => [...prev.slice(-4), toast]); // max 5
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== toast.id));
    }, 5000);
  }, []);

  useEffect(() => {
    listeners.add(addToast);
    return () => { listeners.delete(addToast); };
  }, [addToast]);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`rounded-lg border px-4 py-3 text-sm shadow-lg backdrop-blur-md animate-in slide-in-from-bottom-2 ${
            toast.type === "error"
              ? "border-red-500/30 bg-red-500/10 text-red-300"
              : "border-yellow-500/30 bg-yellow-500/10 text-yellow-300"
          }`}
        >
          <span className="mr-2">{toast.type === "error" ? "⚠️" : "⚡"}</span>
          {toast.message}
        </div>
      ))}
    </div>
  );
}
