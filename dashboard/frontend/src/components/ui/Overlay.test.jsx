import React, { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Dialog } from './Overlay.jsx'

function ControlledDialog() {
  const [value, setValue] = useState('')
  return (
    <Dialog open onClose={() => {}} title="Approve cluster mutation">
      <input
        aria-label="Confirmation target"
        value={value}
        onChange={event => setValue(event.target.value)}
      />
    </Dialog>
  )
}

describe('Overlay focus management', () => {
  it('keeps focus in a controlled input across rerenders', () => {
    render(<ControlledDialog />)
    const input = screen.getByRole('textbox', { name: 'Confirmation target' })
    fireEvent.change(input, { target: { value: 'flagd' } })
    expect(input.value).toBe('flagd')
    expect(document.activeElement).toBe(input)
  })
})
