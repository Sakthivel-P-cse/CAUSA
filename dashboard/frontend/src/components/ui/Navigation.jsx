import React, { useEffect, useId, useRef, useState } from 'react'

export function Tabs({ tabs, value, onChange, label = 'Views' }) {
  const id = useId()
  function onKeyDown(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const current = tabs.findIndex(tab => tab.value === value)
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
    onChange(tabs[next].value)
    event.currentTarget.querySelectorAll('[role="tab"]')[next]?.focus()
  }
  return (
    <div className="ui-tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
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
  const rootRef = useRef(null)
  const triggerRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const first = rootRef.current?.querySelector('[role="menu"] [role="menuitem"]')
    first?.focus()
    function onPointerDown(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  function onKeyDown(event) {
    if (event.key === 'Escape') {
      setOpen(false)
      triggerRef.current?.focus()
      return
    }
    if (!open || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    const items = [...rootRef.current.querySelectorAll('[role="menuitem"]')]
    if (!items.length) return
    event.preventDefault()
    const current = items.indexOf(document.activeElement)
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next].focus()
  }

  return (
    <div className="ui-popover" ref={rootRef} onKeyDown={onKeyDown}>
      {React.cloneElement(trigger, {
        ref: triggerRef,
        'aria-expanded': open,
        'aria-haspopup': 'menu',
        onClick: () => setOpen(value => !value),
      })}
      {open && <div className="ui-popover__panel" role="menu" aria-label={label}>{children}</div>}
    </div>
  )
}
