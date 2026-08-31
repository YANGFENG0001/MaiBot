import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/workspaces'

export interface MemorySpaceItem {
  id: string
  name: string
  description: string
  space_type: 'public' | 'private' | string
  enabled: boolean
  policy_revision: number
}

export interface WorkspaceItem {
  id: string
  name: string
  description: string
  memory_space_id: string
  memory_space_name: string
  persona_profile_id: string | null
  is_default: boolean
  enabled: boolean
  inherit_global_tools: boolean
  inherit_global_plugins: boolean
  policy_revision: number
  member_count: number
  created_at: string
  updated_at: string
}

export interface WorkspaceListResponse {
  success: boolean
  data: WorkspaceItem[]
  memory_spaces: MemorySpaceItem[]
}

export interface WorkspaceCreateInput {
  name: string
  description: string
  memory_mode: 'private' | 'public' | 'existing'
  memory_space_id?: string
  inherit_global_tools: boolean
  inherit_global_plugins: boolean
}

export interface AvailableChatItem {
  session_id: string
  display_name: string
  platform: string
  account_id: string
  chat_type: 'group' | 'private'
  target_id: string
  last_active_timestamp: string | null
  workspace_id: string
  workspace_name: string
  explicitly_assigned: boolean
}

export async function getWorkspaces(): Promise<WorkspaceListResponse> {
  return backendApi.get<WorkspaceListResponse>(API_BASE, {
    cache: 'no-store',
    errorMessage: '读取子系统列表失败',
  })
}

export async function createWorkspace(input: WorkspaceCreateInput): Promise<WorkspaceItem> {
  const response = await backendApi.post<{ success: boolean; data: WorkspaceItem }>(API_BASE, {
    body: input,
    errorMessage: '创建子系统失败',
  })
  return response.data
}

export async function updateWorkspace(
  workspaceId: string,
  input: Partial<Pick<WorkspaceItem, 'name' | 'description' | 'memory_space_id' | 'enabled' | 'inherit_global_tools' | 'inherit_global_plugins'>>,
): Promise<WorkspaceItem> {
  const response = await backendApi.patch<{ success: boolean; data: WorkspaceItem }>(
    `${API_BASE}/${encodeURIComponent(workspaceId)}`,
    { body: input, errorMessage: '更新子系统失败' },
  )
  return response.data
}

export async function getAvailableWorkspaceChats(): Promise<AvailableChatItem[]> {
  const response = await backendApi.get<{ success: boolean; data: AvailableChatItem[] }>(
    `${API_BASE}/chats/available`,
    { cache: 'no-store', errorMessage: '读取聊天列表失败' },
  )
  return response.data
}

export async function assignWorkspaceChats(workspaceId: string, sessionIds: string[]): Promise<number> {
  const response = await backendApi.post<{ success: boolean; assigned_count: number }>(
    `${API_BASE}/${encodeURIComponent(workspaceId)}/members`,
    { body: { session_ids: sessionIds }, errorMessage: '分配聊天失败' },
  )
  return response.assigned_count
}

export interface MemorySpaceAclItem {
  peer_space_id: string
  can_read_from_peer: boolean
  expose_to_peer: boolean
}

export async function createMemorySpace(input: { name: string; description: string; space_type: 'public' | 'private' }): Promise<MemorySpaceItem> {
  return backendApi.post<MemorySpaceItem>(`${API_BASE}/memory-spaces`, {
    body: input,
    errorMessage: '创建记忆空间失败',
  })
}

export async function updateMemorySpace(
  memorySpaceId: string,
  input: Partial<Pick<MemorySpaceItem, 'name' | 'description' | 'enabled'>>,
): Promise<MemorySpaceItem> {
  return backendApi.patch<MemorySpaceItem>(`${API_BASE}/memory-spaces/${encodeURIComponent(memorySpaceId)}`, {
    body: input,
    errorMessage: '更新记忆空间失败',
  })
}

export async function getMemorySpaceAcl(memorySpaceId: string): Promise<MemorySpaceAclItem[]> {
  return backendApi.get<MemorySpaceAclItem[]>(`${API_BASE}/memory-spaces/${encodeURIComponent(memorySpaceId)}/acl`, {
    cache: 'no-store',
    errorMessage: '读取记忆空间权限失败',
  })
}

export async function setMemorySpaceAcl(
  memorySpaceId: string,
  peerSpaceId: string,
  input: Pick<MemorySpaceAclItem, 'can_read_from_peer' | 'expose_to_peer'>,
): Promise<MemorySpaceAclItem> {
  return backendApi.put<MemorySpaceAclItem>(
    `${API_BASE}/memory-spaces/${encodeURIComponent(memorySpaceId)}/acl/${encodeURIComponent(peerSpaceId)}`,
    { body: input, errorMessage: '更新记忆空间权限失败' },
  )
}

export async function migrateLegacyMemoryGroups(): Promise<number> {
  const response = await backendApi.post<{ success: boolean; assigned_count: number }>(
    `${API_BASE}/memory-spaces/migrate-legacy`,
    { errorMessage: '迁移旧共享记忆组失败' },
  )
  return response.assigned_count
}
