import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { setBearerToken } from '../api/client'

afterEach(() => {
  cleanup()
  setBearerToken('')
  vi.restoreAllMocks()
})

if (!URL.createObjectURL) {
  URL.createObjectURL = () => 'blob:mock'
}
