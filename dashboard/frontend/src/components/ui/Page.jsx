import React from 'react'
import { Search } from 'lucide-react'
import { Input, Select } from './FormControls.jsx'

export function MetricTile({ label, value, change, tone = 'neutral', icon }) {
  return <article className={`ui-metric ui-metric--${tone}`}>{icon}<span>{label}</span><strong>{value}</strong>{change && <small>{change}</small>}</article>
}

export function ChartFrame({ title, description, actions, loading, children }) {
  return <section className="ui-chart"><header><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{actions}</header><div className="ui-chart__body" aria-busy={loading}>{children}</div></section>
}

export function PageHeader({ title, description, eyebrow, actions }) {
  return <header className="ui-page-header"><div>{eyebrow && <span>{eyebrow}</span>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="ui-page-header__actions">{actions}</div>}</header>
}

export function QueryBar({ value, onChange, placeholder = 'Search or filter…', children }) {
  return <div className="ui-query-bar"><Search size={16} aria-hidden="true" /><Input aria-label="Query" value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} />{children}</div>
}

export function TimeRangePicker({ filters, setFilter }) {
  const absolute = Boolean(filters.from && filters.to)
  return (
    <div className="ui-time-range">
      <Select aria-label="Time range" value={absolute ? 'absolute' : filters.minutes} onChange={event => {
        if (event.target.value !== 'absolute') setFilter('minutes', Number(event.target.value))
        else {
          const now = new Date()
          setFilter('range', { from: new Date(now.getTime() - filters.minutes * 60_000).toISOString(), to: now.toISOString() })
        }
      }}>
        <option value={15}>Last 15 minutes</option><option value={30}>Last 30 minutes</option>
        <option value={60}>Last hour</option><option value={360}>Last 6 hours</option>
        <option value={1440}>Last 24 hours</option><option value="absolute">Absolute range</option>
      </Select>
      {absolute && <>
        <Input aria-label="Start time" type="datetime-local" value={filters.from.slice(0, 16)} onChange={event => setFilter('from', new Date(event.target.value).toISOString())} />
        <Input aria-label="End time" type="datetime-local" value={filters.to.slice(0, 16)} onChange={event => setFilter('to', new Date(event.target.value).toISOString())} />
      </>}
    </div>
  )
}

