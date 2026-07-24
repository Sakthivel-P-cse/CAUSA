import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

const PREFERENCES_KEY = 'causa.preferences'
const VALID_MINUTES = [15, 30, 60, 120, 360, 1440]
const VALID_REFRESH = [0, 15, 30, 60]
const DEFAULT_FILTERS = { service: '', minutes: 360, refreshSec: 0, from: '', to: '' }
const DEFAULT_PREFERENCES = { theme: 'dark', density: 'comfortable', sidebarCollapsed: false }

const DashboardContext = createContext(null)

function loadPreferences() {
  try {
    const parsed = JSON.parse(window.localStorage?.getItem(PREFERENCES_KEY) || '{}')
    return {
      theme: ['dark', 'light', 'system'].includes(parsed.theme) ? parsed.theme : DEFAULT_PREFERENCES.theme,
      density: ['comfortable', 'compact'].includes(parsed.density) ? parsed.density : DEFAULT_PREFERENCES.density,
      sidebarCollapsed: Boolean(parsed.sidebarCollapsed),
    }
  } catch {
    return DEFAULT_PREFERENCES
  }
}

export function DashboardProvider({ children }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [preferences, setPreferences] = useState(loadPreferences)

  const filters = useMemo(() => {
    const rawMinutes = Number(searchParams.get('minutes'))
    const rawRefresh = Number(searchParams.get('refresh'))
    const from = searchParams.get('from') || ''
    const to = searchParams.get('to') || ''
    return {
      service: (searchParams.get('service') || '').slice(0, 120),
      minutes: VALID_MINUTES.includes(rawMinutes) ? rawMinutes : DEFAULT_FILTERS.minutes,
      refreshSec: VALID_REFRESH.includes(rawRefresh) ? rawRefresh : DEFAULT_FILTERS.refreshSec,
      from: from && !Number.isNaN(Date.parse(from)) ? from : '',
      to: to && !Number.isNaN(Date.parse(to)) ? to : '',
    }
  }, [searchParams])

  useEffect(() => {
    try {
      window.localStorage?.setItem(PREFERENCES_KEY, JSON.stringify(preferences))
    } catch {
      // Preferences still work for this session when storage is unavailable.
    }
    const systemDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
    const resolvedTheme = preferences.theme === 'system' ? (systemDark ? 'dark' : 'light') : preferences.theme
    document.documentElement.dataset.theme = resolvedTheme
    document.documentElement.dataset.density = preferences.density
  }, [preferences])

  const setFilter = useCallback((key, value) => {
    setSearchParams(previous => {
      const next = new URLSearchParams(previous)
      if (key === 'range') {
        next.delete('minutes')
        next.set('from', value.from)
        next.set('to', value.to)
        return next
      }
      if (value === '' || value == null || (key === 'refreshSec' && Number(value) === 0)) {
        next.delete(key === 'refreshSec' ? 'refresh' : key)
      } else {
        next.set(key === 'refreshSec' ? 'refresh' : key, String(value))
      }
      if (key === 'minutes') {
        next.delete('from')
        next.delete('to')
      }
      if (key === 'from' || key === 'to') next.delete('minutes')
      return next
    }, { replace: true })
  }, [setSearchParams])

  const setPreference = useCallback((key, value) => {
    setPreferences(previous => ({ ...previous, [key]: value }))
  }, [])

  const value = useMemo(() => ({
    filters,
    preferences,
    setFilter,
    setPreference,
    resetFilters: () => setSearchParams({}, { replace: true }),
    timeMode: filters.from && filters.to ? 'absolute' : 'relative',
  }), [filters, preferences, setFilter, setPreference, setSearchParams])

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>
}

export function useDashboard() {
  const ctx = useContext(DashboardContext)
  if (!ctx) throw new Error('useDashboard must be used within DashboardProvider')
  return ctx
}
