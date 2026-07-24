import React, { forwardRef, useId } from 'react'

function Field({ label, hint, error, id, children }) {
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  return (
    <div className="ui-field">
      {label && <label htmlFor={id}>{label}</label>}
      {children}
      {hint && <span id={hintId} className="ui-field__hint">{hint}</span>}
      {error && <span id={errorId} className="ui-field__error" role="alert">{error}</span>}
    </div>
  )
}

export const Input = forwardRef(function Input({ label, hint, error, id: suppliedId, className = '', ...props }, ref) {
  const generatedId = useId()
  const id = suppliedId || generatedId
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ') || undefined
  return (
    <Field {...{ label, hint, error, id }}>
      <input ref={ref} id={id} className={`ui-input ${className}`.trim()} aria-invalid={Boolean(error)} aria-describedby={describedBy} {...props} />
    </Field>
  )
})

export const Select = forwardRef(function Select({ label, hint, error, options = [], id: suppliedId, children, className = '', ...props }, ref) {
  const generatedId = useId()
  const id = suppliedId || generatedId
  return (
    <Field {...{ label, hint, error, id }}>
      <select ref={ref} id={id} className={`ui-select ${className}`.trim()} aria-invalid={Boolean(error)} {...props}>
        {children || options.map(option => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </Field>
  )
})

