import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * App-level error boundary. A malformed agent event must never blank the whole
 * page — it shows a recoverable banner instead. (We learned this the hard way:
 * a list item with name=null threw in render and took the entire tree down.)
 */
interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surfaced in the browser console for debugging during the demo.
    console.error("Aisle UI error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app">
          <header className="app-header">
            <span className="brand">Aisle</span>
          </header>
          <main className="fallback">
            <div className="error-banner">
              Something hiccuped rendering the last update.
            </div>
            <p style={{ color: "var(--ink-soft)", textAlign: "center", maxWidth: 480 }}>
              {this.state.error.message}
            </p>
            <button className="btn primary" onClick={() => this.setState({ error: null })}>
              Dismiss & continue
            </button>
          </main>
        </div>
      );
    }
    return this.props.children;
  }
}
