import React, { useId, useState } from 'react'

export function Tabs({ tabs, value, onChange, label = 'Views' }) {
  const id = useId()
  return (
    <div className="ui-tabs" role="tablist" aria-label={label}>
      {tabs.map(tab => (
        <button
          key={tab.value}
          id={`${id}-${tab.value}`}
          type="button"
          role="tab"
          aria-selected={value === tab.value}
          tabIndex={value === tab.value ? 0 : -1}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export function Tooltip({ content, children }) {
  const id = useId()
  return <span className="ui-tooltip" aria-describedby={id}>{children}<span id={id} role="tooltip">{content}</span></span>
}

export function Popover({ trigger, children, label = 'Menu' }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="ui-popover" onKeyDown={event => event.key === 'Escape' && setOpen(false)}>
      {React.cloneElement(trigger, { 'aria-expanded': open, 'aria-haspopup': true, onClick: () => setOpen(value => !value) })}
      {open && <div className="ui-popover__panel" role="menu" aria-label={label}>{children}</div>}
    </div>
  )
}

