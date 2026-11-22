import type { User } from '../features/administration/types'

export interface QuickAccount {
  username: string
  display_name: string
  role: string
  password?: string
}

export interface WorkspaceInfo {
  name: string
  mode: string
  tenant_code: string
  quick_accounts?: QuickAccount[]
}

export interface Dashboard {
  documents: number
  tasks: number
  review: number
  completed: number
  teams: string[]
  model_ready: boolean
  business_ready: boolean
}

export interface LoginResponse {
  user: User
}
