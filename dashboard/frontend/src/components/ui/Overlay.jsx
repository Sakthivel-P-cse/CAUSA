import React, { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { Button } from './Button.jsx'

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function Overlay({ open, onClose, title, children, variant }) {
  const titleId = useId()
  const panelRef = useRef(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return undefined
    const previousFocus = document.activeElement
    const panel = panelRef.current
    const preferredFocus = panel?.querySelector(
      'input:not([disabled]), textarea:not([disabled]), select:not([disabled])',
    )
    ;(preferredFocus || panel?.querySelector(FOCUSABLE))?.focus()
    function onKeyDown(event) {
      if (event.key === 'Escape') onCloseRef.current()
      if (event.key !== 'Tab') return
      const items = [...panel.querySelectorAll(FOCUSABLE)]
      if (!items.length) return
      const first = items[0]
      const last = items.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    document.body.classList.add('overlay-open')
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.classList.remove('overlay-open')
      previousFocus?.focus()
    }
  }, [open])

  if (!open) return null
  return createPortal(
    <div className="ui-overlay" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <section ref={panelRef} className={`ui-overlay__panel ui-overlay__panel--${variant}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header><h2 id={titleId}>{title}</h2><Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X size={18} /></Button></header>
        <div className="ui-overlay__content">{children}</div>
      </section>
    </div>,
    document.body,
  )
}

export function Dialog(props) { return <Overlay variant="dialog" {...props} /> }
export function Drawer(props) { return <Overlay variant="drawer" {...props} /> }
