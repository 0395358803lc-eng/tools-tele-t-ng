import { useEffect, useState } from 'react'

export function useTheme() {
  const [theme, setTheme] = useState('dark')
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') root.classList.add('dark')
    else root.classList.remove('dark')
  }, [theme])
  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  return { theme, toggle, setTheme }
}
