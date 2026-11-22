export type ApiClient = <T>(path: string, method?: string, payload?: unknown) => Promise<T>

export type ApiReader = <T>(path: string) => Promise<T>

export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
}

export interface Items<T> {
  items: T[]
}

export interface CreatedResource {
  id: string
}
