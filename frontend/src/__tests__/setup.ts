import '@testing-library/jest-dom/vitest'

if (typeof globalThis.localStorage === 'undefined') {
  const values = new Map<string, string>()
  const memoryStorage: Storage = {
    get length() { return values.size },
    clear: () => values.clear(),
    getItem: (key) => values.get(String(key)) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(String(key)) },
    setItem: (key, value) => { values.set(String(key), String(value)) },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: memoryStorage,
  })
}
