"use client";

import React, { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error(`[ErrorBoundary${this.props.name ? `: ${this.props.name}` : ""}]`, error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 text-sm">
          <div className="font-medium text-red-400 mb-1">
            ⚠️ Ошибка в компоненте{this.props.name ? ` «${this.props.name}»` : ""}
          </div>
          <div className="text-xs text-red-400/60 font-mono break-all">
            {this.state.error?.message || "Unknown error"}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
