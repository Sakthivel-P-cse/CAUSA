import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  MemoryRouter,
  Route,
  Routes,
  useParams,
} from 'react-router-dom'

function Incident() {
  const { incidentId } = useParams()
  return <p>Incident {incidentId}</p>
}

describe('application router dependency', () => {
  it('matches and decodes route parameters', () => {
    render(
      <MemoryRouter initialEntries={['/incidents/inc%201']}>
        <Routes>
          <Route path="/incidents/:incidentId" element={<Incident />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText('Incident inc 1')).toBeTruthy()
  })
})
