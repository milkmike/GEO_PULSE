"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary${this.props.name ? `:${this.props.name}` : ""}]`, error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div className="flex flex-col items-center justify-center py-16 px-4">
          <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-8 max-w-lg text-center">
            <div className="text-4xl mb-4">💥</div>
            <h2 className="text-lg font-semibold text-foreground mb-2">Что-то пошло не так</h2>
            <p className="text-sm text-muted-foreground mb-4">
              {this.state.error?.message || "Неизвестная ошибка"}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm text-foreground hover:bg-white/10 transition-colors"
            >
              🔄 Попробовать снова
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
