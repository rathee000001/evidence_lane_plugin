import { Component, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './design-system/glass-shell.css';
import './design-system/theme/sqlite-glass-theme.css';
import './studio.css';

class StudioBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = {failed: false};
  static getDerivedStateFromError() { return {failed: true}; }
  render() { return this.state.failed ? <main className="studio-fatal"><h1>Studio could not render this view</h1><p>Reload the window to reconnect. Your project state remains in the engine.</p><button onClick={() => location.reload()}>Reload Studio</button></main> : this.props.children; }
}

createRoot(document.getElementById('root')!).render(<StudioBoundary><App /></StudioBoundary>);
