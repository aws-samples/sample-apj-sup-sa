// App-level error boundary. Any unhandled render/lifecycle error used to unmount the entire React
// tree and leave a blank page (the failure mode of the NUMERIC-string score bug) — this catches it
// and shows a recoverable message instead. State and the conversation on the server are unaffected;
// "Reload" simply re-mounts the app.

import { Component, type ReactNode } from "react";
import { reportClientEvent } from "../lib/clientEvents";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: unknown) {
    // Log for diagnostics only — never render raw error internals to the user.
    console.error("Unhandled render error:", error);
    // Tell the server a render crash happened (event name only — no error details leave here).
    reportClientEvent("render_error");
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="app">
          <div className="pad">
            <div className="center-wrap">
              <h2 className="title">Something went wrong</h2>
              <p className="alert" role="alert">
                The screen hit an unexpected error. Your session data is safe — reloading usually
                fixes it.
              </p>
              <button
                className="btn primary"
                style={{ marginTop: 12 }}
                onClick={() => window.location.reload()}
              >
                Reload
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
