import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/memory-permission-groups'

export interface GroupCounts {
  members: number
  contexts: number
  capabilities: number
  rules: number
  bot_rules: number
}

export interface PermissionGroupItem {
  id: string
  name: string
  description: string
  enabled: boolean
  priority: number
  memory_scope_mode: 'inherit' | 'override' | string
  is_manager_mode: boolean
  policy_revision: number
  counts: GroupCounts
  created_at: string
  updated_at: string
}

export interface PermissionGroupCatalog {
  capabilities: string[]
  manager_preset_capabilities: string[]
  scope_types: string[]
  space_selectors: string[]
  partition_types: string[]
  partition_selectors: string[]
  bot_selectors: string[]
}

export interface PermissionGroupListResponse extends PermissionGroupCatalog {
  success: boolean
  data: PermissionGroupItem[]
}

export interface GroupMemberItem {
  person_id: string
  person_name: string
  platform: string
  account_id: string
}

export interface GroupContextItem {
  id: number
  scope_type: 'global' | 'workspace' | 'session' | 'channel' | string
  workspace_id: string | null
  session_id: string | null
  channel_type: string | null
  allow_group_disclosure: boolean
  enabled: boolean
}

export interface GroupCapabilityItem {
  capability: string
  enabled: boolean
}

export interface GroupRuleItem {
  id: number
  effect: 'allow' | 'deny' | string
  space_selector: string
  memory_space_id: string | null
  partition_type: string
  partition_selector: string
  partition_key: string | null
  memory_types: unknown[]
  tags: unknown[]
  sensitivity_max: number | null
  time_start: string | null
  time_end: string | null
  priority: number
  enabled: boolean
}

export interface GroupBotRuleItem {
  id: number
  effect: string
  bot_selector: string
  bot_profile_id: string | null
}

export interface PermissionGroupDetail {
  success: boolean
  data: PermissionGroupItem
  members: GroupMemberItem[]
  contexts: GroupContextItem[]
  capabilities: GroupCapabilityItem[]
  rules: GroupRuleItem[]
  bot_rules: GroupBotRuleItem[]
}

export interface PersonOption {
  person_id: string
  person_name: string
  platform: string
  account_id: string
  user_nickname: string
}

export interface SimulateScope {
  space_ids: string[]
  partition_ids: string[]
}

export interface SimulateResult {
  success: boolean
  allowed: boolean
  denied_reason: string
  trace_id: string
  workspace_id: string
  workspace_name: string
  active_bot_profile_id: string
  active_bot_profile_type: string
  bot_profile_name: string
  permission_group_id: string
  permission_group_name: string
  matched_context: GroupContextItem | null
  access_mode: string
  security_domain: string
  policy_revision: number
  capabilities: string[]
  requested_scope: SimulateScope
  allowed_scope: SimulateScope
  denied_scope: SimulateScope
  writable_partition_ids: string[]
  allow_group_disclosure: boolean
}

export interface GroupCreateInput {
  name: string
  description?: string
  priority?: number
  memory_scope_mode?: 'inherit' | 'override'
  is_manager_mode?: boolean
  enabled?: boolean
}

export async function getPermissionGroups(): Promise<PermissionGroupListResponse> {
  return backendApi.get<PermissionGroupListResponse>(API_BASE, {
    cache: 'no-store',
    errorMessage: '读取权限组失败',
  })
}

export async function getPermissionGroup(groupId: string): Promise<PermissionGroupDetail> {
  return backendApi.get<PermissionGroupDetail>(`${API_BASE}/${encodeURIComponent(groupId)}`, {
    cache: 'no-store',
    errorMessage: '读取权限组详情失败',
  })
}

export async function createPermissionGroup(input: GroupCreateInput): Promise<PermissionGroupItem> {
  return backendApi.post<PermissionGroupItem>(API_BASE, {
    body: input,
    errorMessage: '创建权限组失败',
  })
}

export async function updatePermissionGroup(
  groupId: string,
  input: Partial<GroupCreateInput> & { expected_revision?: number },
): Promise<PermissionGroupItem> {
  return backendApi.patch<PermissionGroupItem>(`${API_BASE}/${encodeURIComponent(groupId)}`, {
    body: input,
    errorMessage: '更新权限组失败',
  })
}

export async function deletePermissionGroup(groupId: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(groupId)}`,
    { errorMessage: '删除权限组失败' },
  )
  return response.removed
}

export async function addGroupMember(
  groupId: string,
  personId: string,
  expectedRevision?: number,
): Promise<GroupMemberItem[]> {
  return backendApi.post<GroupMemberItem[]>(`${API_BASE}/${encodeURIComponent(groupId)}/members`, {
    body: { person_id: personId, expected_revision: expectedRevision },
    errorMessage: '添加成员失败',
  })
}

export async function removeGroupMember(groupId: string, personId: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(groupId)}/members/${encodeURIComponent(personId)}`,
    { errorMessage: '移除成员失败' },
  )
  return response.removed
}

export async function addGroupContext(
  groupId: string,
  input: {
    scope_type: 'global' | 'workspace' | 'session' | 'channel'
    workspace_id?: string | null
    session_id?: string | null
    channel_type?: 'private' | 'group' | null
    allow_group_disclosure?: boolean
    expected_revision?: number
  },
): Promise<GroupContextItem[]> {
  return backendApi.post<GroupContextItem[]>(`${API_BASE}/${encodeURIComponent(groupId)}/contexts`, {
    body: input,
    errorMessage: '添加上下文失败',
  })
}

export async function removeGroupContext(groupId: string, contextId: number): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(groupId)}/contexts/${contextId}`,
    { errorMessage: '移除上下文失败' },
  )
  return response.removed
}

export async function setGroupCapability(
  groupId: string,
  capability: string,
  enabled: boolean,
  expectedRevision?: number,
): Promise<GroupCapabilityItem[]> {
  return backendApi.put<GroupCapabilityItem[]>(
    `${API_BASE}/${encodeURIComponent(groupId)}/capabilities/${encodeURIComponent(capability)}`,
    { body: { enabled, expected_revision: expectedRevision }, errorMessage: '更新能力失败' },
  )
}

export async function removeGroupCapability(groupId: string, capability: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(groupId)}/capabilities/${encodeURIComponent(capability)}`,
    { errorMessage: '移除能力失败' },
  )
  return response.removed
}

export interface RuleCreateInput {
  effect: 'allow' | 'deny'
  space_selector: 'current' | 'public' | 'specific' | 'all_normal'
  memory_space_id?: string | null
  partition_type: 'any' | 'shared' | 'person' | 'conversation'
  partition_selector: 'any' | 'self' | 'current' | 'specific'
  partition_key?: string | null
  priority?: number
  enabled?: boolean
}

export async function addGroupRule(groupId: string, input: RuleCreateInput): Promise<GroupRuleItem[]> {
  return backendApi.post<GroupRuleItem[]>(`${API_BASE}/${encodeURIComponent(groupId)}/rules`, {
    body: input,
    errorMessage: '新增记忆规则失败',
  })
}

export async function deleteGroupRule(groupId: string, ruleId: number): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(groupId)}/rules/${ruleId}`,
    { errorMessage: '删除记忆规则失败' },
  )
  return response.removed
}

export async function setGroupBotRule(
  groupId: string,
  input: {
    effect: 'allow' | 'deny'
    bot_selector: 'public' | 'current_group' | 'kami' | 'specific'
    bot_profile_id?: string | null
    expected_revision?: number
  },
): Promise<GroupBotRuleItem[]> {
  return backendApi.put<GroupBotRuleItem[]>(`${API_BASE}/${encodeURIComponent(groupId)}/bot-rules`, {
    body: input,
    errorMessage: '更新 Bot 规则失败',
  })
}

export async function removeGroupBotRule(groupId: string, botRuleId: number): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(groupId)}/bot-rules/${botRuleId}`,
    { errorMessage: '删除 Bot 规则失败' },
  )
  return response.removed
}

export async function listPersonOptions(): Promise<PersonOption[]> {
  const response = await backendApi.get<{ success: boolean; data: PersonOption[] }>(
    `${API_BASE}/persons`,
    { cache: 'no-store', errorMessage: '读取人员列表失败' },
  )
  return response.data
}

export async function simulateAccess(input: {
  session_id: string
  person_id: string
  audience_type?: 'private' | 'group'
  bot_profile_id?: string
}): Promise<SimulateResult> {
  return backendApi.post<SimulateResult>(`${API_BASE}/simulate`, {
    body: input,
    errorMessage: '权限模拟失败',
  })
}
