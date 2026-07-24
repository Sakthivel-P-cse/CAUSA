import React from 'react'
import { AlertTriangle, Inbox, LoaderCircle } from 'lucide-react'
import { Button } from './Button.jsx'

export function Chip({ children, tone = 'neutral', className = '', ...props }) {
  return <span className={`ui-chip ui-chip--${tone} ${className}`.trim()} {...props}>{children}</span>
}

export function Status({ status = 'unknown', label = status }) {
  const tone = { healthy: 'success', resolved: 'success', warning: 'warning', critical: 'danger', open: 'danger' }[status] || 'neutral'
  return <Chip tone={tone}><span className="ui-status-dot" aria-hidden="true" />{label}</Chip>
}

export function Skeleton({ width, height, className = '', ...props }) {
  return <span className={`ui-skeleton ${className}`.trim()} style={{ width, height }} aria-hidden="true" {...props} />
}

export function LoadingState({ label = 'Loading' }) {
  return <div className="ui-state" role="status"><LoaderCircle className="ui-spin" aria-hidden="true" />{label}</div>
}

export function EmptyState({ title = 'No results', description, action }) {
  return <div className="ui-state"><Inbox aria-hidden="true" /><strong>{title}</strong>{description && <p>{description}</p>}{action}</div>
}

export function ErrorState({ title = 'Something went wrong', error, onRetry }) {
  return (
    <div className="ui-state ui-state--error" role="alert">
      <AlertTriangle aria-hidden="true" /><strong>{title}</strong>
      {error && <p>{typeof error === 'string' ? error : error.message}</p>}
      {onRetry && <Button onClick={onRetry}>Try again</Button>}
    </div>
  )
}

