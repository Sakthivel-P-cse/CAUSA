import React from 'react'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Dashboard render failure', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="error-boundary" role="alert">
        <h1>The dashboard could not render</h1>
        <p>Reload the page to recover. If this continues, check the dashboard logs.</p>
        <button className="btn primary" onClick={() => window.location.reload()}>
          Reload dashboard
        </button>
      </main>
    )
  }
}
