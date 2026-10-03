import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/bot-profiles'

export interface LineageItem {
  id: string
  name: string
  profile_type: string
}

export interface ToolPolicyItem {
  component_name: string
  effect: string
}

export interface PluginPolicyItem {
  plugin_id: string
  effect: string
  overrides: Record<string, unknown>
}

export interface MemoryRuleItem {
  target_space_id: string
  target_space_name: string
  can_read: boolean
  filters: Record<string, unknown>
}

export interface BotProfileItem {
  id: string
  name: string
  profile_type: 'public' | 'group' | 'kami' | string
  parent_profile_id: string | null
  persona_profile_id: string | null
  home_memory_space_id: string
  home_memory_space_name: string
  inherit_parent_persona: boolean
  inherit_parent_tools: boolean
  inherit_parent_plugins: boolean
  enabled: boolean
  is_system: boolean
  policy_revision: number
  lineage: LineageItem[]
  tool_policies: ToolPolicyItem[]
  plugin_policies: PluginPolicyItem[]
  memory_rules: MemoryRuleItem[]
  created_at: string
  updated_at: string
}

export interface BotProfileUpdateInput {
  parent_profile_id?: string | null
  persona_profile_id?: string | null
  inherit_parent_persona?: boolean
  inherit_parent_tools?: boolean
  inherit_parent_plugins?: boolean
  enabled?: boolean
  expected_revision?: number
}

export async function getBotProfiles(): Promise<BotProfileItem[]> {
  const response = await backendApi.get<{ success: boolean; data: BotProfileItem[] }>(API_BASE, {
    cache: 'no-store',
    errorMessage: '读取 Bot 配置失败',
  })
  return response.data
}

export async function updateBotProfile(
  profileId: string,
  input: BotProfileUpdateInput,
): Promise<BotProfileItem> {
  const response = await backendApi.patch<{ success: boolean; data: BotProfileItem }>(
    `${API_BASE}/${encodeURIComponent(profileId)}`,
    { body: input, errorMessage: '更新 Bot 配置失败' },
  )
  return response.data
}

export async function setBotProfileTool(
  profileId: string,
  componentName: string,
  effect: 'allow' | 'deny',
  expectedRevision?: number,
): Promise<ToolPolicyItem> {
  return backendApi.put<ToolPolicyItem>(
    `${API_BASE}/${encodeURIComponent(profileId)}/tools/${encodeURIComponent(componentName)}`,
    { body: { effect, expected_revision: expectedRevision }, errorMessage: '更新工具策略失败' },
  )
}

export async function removeBotProfileTool(profileId: string, componentName: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(profileId)}/tools/${encodeURIComponent(componentName)}`,
    { errorMessage: '删除工具策略失败' },
  )
  return response.removed
}

export async function setBotProfilePlugin(
  profileId: string,
  pluginId: string,
  effect: 'allow' | 'deny' | 'inherit',
  expectedRevision?: number,
): Promise<PluginPolicyItem> {
  return backendApi.put<PluginPolicyItem>(
    `${API_BASE}/${encodeURIComponent(profileId)}/plugins/${encodeURIComponent(pluginId)}`,
    { body: { effect, expected_revision: expectedRevision }, errorMessage: '更新插件策略失败' },
  )
}

export async function removeBotProfilePlugin(profileId: string, pluginId: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(profileId)}/plugins/${encodeURIComponent(pluginId)}`,
    { errorMessage: '删除插件策略失败' },
  )
  return response.removed
}

export async function setBotProfileMemoryRule(
  profileId: string,
  targetSpaceId: string,
  canRead: boolean,
  expectedRevision?: number,
): Promise<MemoryRuleItem> {
  return backendApi.put<MemoryRuleItem>(
    `${API_BASE}/${encodeURIComponent(profileId)}/memory-rules/${encodeURIComponent(targetSpaceId)}`,
    { body: { can_read: canRead, expected_revision: expectedRevision }, errorMessage: '更新记忆读取规则失败' },
  )
}

export async function removeBotProfileMemoryRule(profileId: string, targetSpaceId: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(profileId)}/memory-rules/${encodeURIComponent(targetSpaceId)}`,
    { errorMessage: '删除记忆读取规则失败' },
  )
  return response.removed
}
