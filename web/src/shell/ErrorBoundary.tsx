import { Component, type ReactNode } from "react";

/** Shows a render error instead of a blank screen, with the text to copy into a bug report. */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string; compact?: boolean }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error(`[${this.props.label}]`, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="boundary" role="alert">
        <strong>{this.props.label}: ошибка отображения</strong>
        <pre>{error.stack || error.message}</pre>
        <button onClick={() => this.setState({ error: null })}>Повторить</button>
        {!this.props.compact && <button onClick={() => { try { localStorage.clear(); } catch { /* ignore */ } location.reload(); }}>Сбросить настройки и перезагрузить</button>}
      </div>
    );
  }
}
