import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button.jsx'
import { Popover, Tabs } from './Navigation.jsx'

describe('shared navigation controls', () => {
  it('moves tab selection with arrow keys', () => {
    const onChange = vi.fn()
    render(<Tabs tabs={[{ value: 'one', label: 'One' }, { value: 'two', label: 'Two' }]} value="one" onChange={onChange} />)
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith('two')
  })

  it('closes a popover with Escape and restores trigger focus', () => {
    render(
      <Popover trigger={<Button>Preferences</Button>}>
        <Button role="menuitem">Action</Button>
      </Popover>,
    )
    const trigger = screen.getByRole('button', { name: 'Preferences' })
    fireEvent.click(trigger)
    const action = screen.getByRole('menuitem')
    fireEvent.keyDown(action, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
})
