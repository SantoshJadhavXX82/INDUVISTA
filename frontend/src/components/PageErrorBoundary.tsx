/**
 * PageErrorBoundary — catches render errors below it and shows a
 * recoverable card instead of unmounting to a white screen.
 *
 * Born from a real incident: a ReferenceError in one row component
 * blanked the entire Calc tags page with zero on-screen feedback.
 * Wrap any page's default export:
 *
 *   export default function MyPage() {
 *     return (
 *       <PageErrorBoundary page="My page">
 *         <MyPageInner />
 *       </PageErrorBoundary>
 *     );
 *   }
 *
 * "Try again" re-renders the subtree (enough for transient errors);
 * "Reload page" is the hard reset. The error message + component
 * stack are shown so a screenshot is a complete bug report.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw, RefreshCw } from "lucide-react";

interface Props {
  /** Page name shown in the fallback title. */
  page?: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
  componentStack: string | null;
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the console error too - DevTools stays the source of truth.
    console.error(`[PageErrorBoundary] ${this.props.page ?? "page"} crashed:`, error, info);
    this.setState({ componentStack: info.componentStack ?? null });
  }

  render() {
    const { error, componentStack } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="p-6 flex justify-center">
        <div className="max-w-2xl w-full border border-red-300 bg-red-50 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-red-700 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-red-900">
                {this.props.page ?? "This page"} hit an error and stopped rendering
              </div>
              <p className="text-xs text-red-800 mt-1">
                The rest of the application is unaffected. Try again, or reload
                the page. If it repeats, screenshot this card - it contains the
                full diagnosis.
              </p>
              <pre className="mt-2 p-2 bg-white/70 border border-red-200 rounded
                              text-[11px] font-mono text-red-900 overflow-x-auto whitespace-pre-wrap">
                {error.message || String(error)}
              </pre>
              {componentStack && (
                <details className="mt-1">
                  <summary className="text-[11px] text-red-800 cursor-pointer">
                    Component stack
                  </summary>
                  <pre className="mt-1 p-2 bg-white/70 border border-red-200 rounded
                                  text-[10px] font-mono text-red-900 overflow-x-auto whitespace-pre-wrap">
                    {componentStack}
                  </pre>
                </details>
              )}
              <div className="mt-3 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => this.setState({ error: null, componentStack: null })}
                  className="text-xs px-2.5 py-1 rounded border border-red-300
                             bg-white hover:bg-red-100 inline-flex items-center gap-1.5"
                >
                  <RotateCcw className="h-3 w-3" />
                  Try again
                </button>
                <button
                  type="button"
                  onClick={() => window.location.reload()}
                  className="text-xs px-2.5 py-1 rounded border border-red-300
                             bg-white hover:bg-red-100 inline-flex items-center gap-1.5"
                >
                  <RefreshCw className="h-3 w-3" />
                  Reload page
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
