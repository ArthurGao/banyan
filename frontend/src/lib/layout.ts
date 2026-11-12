import { useEffect, useState } from 'react'

// Breakpoints of design §11.1: three panes from 1200px, two from 768px, one below.
export type Layout = 'one' | 'two' | 'three'

export function layoutFor(width: number): Layout {
  if (width >= 1200) return 'three'
  if (width >= 768) return 'two'
  return 'one'
}

export function useLayout(): Layout {
  const [width, setWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const onResize = () => {
      setWidth(window.innerWidth)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
    }
  }, [])
  return layoutFor(width)
}
