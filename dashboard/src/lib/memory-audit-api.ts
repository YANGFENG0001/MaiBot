import { backendApi } from '@/lib/http'

const AUDIT_BASE = '/api/webui'
const KAMI_BASE = '/api/webui/kami'

export interface MemoryAccessScope {
  home_space_id?: string
  audience_type?: string
  space_ids?: string[]
  partition_ids?: string[]
  writable_partition_ids?: string[]
}

export interface MemoryAccessAuditItem {
  id: number
  trace_id: string
  session_id: string
  person_id: string
  workspace_id: string
  active_bot_profile_id: string
  permission_group_id: string
  access_mode: string
  security_domain: string
  policy_revision: number
  decision_reason: string
  query_hash: string
  requested_scope: MemoryAccessScope
  allowed_scope: MemoryAccessScope
  denied_scope: MemoryAccessScope
  result_count: number
  latency_ms: number
  created_at: string
  redacted: boolean
}

export interface BotControlAuditItem {
  id: number
  session_id: string
  person_id: string
  platform: string
  command: string
  before_bot_profile_id: string
  after_bot_profile_id: string
  permission_group_id: string
  result: string
  reason: string
  metadata: Record<string, unknown>
  created_at: string
  redacted: boolean
}

export interface KamiSessionItem {
  id: string
  session_id: string
  person_id: string
  kami_bot_profile_id: string
  activated_from_bot_profile_id: string
  permission_group_id: string
  status: string
  activated_at: string
  expires_at: string
  last_used_at: string
  revision: number
  expired: boolean
}

export interface KamiProfileItem {
  id: string
  name: string
  profile_type: string
  persona_profile_id: string | null
  home_memory_space_id: string
  inherit_parent_persona: boolean
  inherit_parent_tools: boolean
  inherit_parent_plugins: boolean
  enabled: boolean
  is_system: boolean
  policy_revision: number
  dangerous: boolean
  max_ttl_seconds: number
  default_ttl_seconds: number
}

export interface AuditQuery {
  session_id?: string
  person_id?: string
  limit?: number
  offset?: number
  reveal?: boolean
}

export async function getMemoryAccessAudit(query: AuditQuery & { access_mode?: string; trace_id?: string } = {}) {
  return backendApi.get<{
    success: boolean
    data: MemoryAccessAuditItem[]
    limit: number
    offset: number
    total: number
  }>(`${AUDIT_BASE}/memory-access-audit`, {
    query: {
      session_id: query.session_id ?? '',
      person_id: query.person_id ?? '',
      access_mode: query.access_mode ?? '',
      trace_id: query.trace_id ?? '',
      reveal: query.reveal ?? false,
      limit: query.limit ?? 100,
      offset: query.offset ?? 0,
    },
    cache: 'no-store',
    errorMessage: '读取记忆访问审计失败',
  })
}

export async function getBotControlAudit(
  query: AuditQuery & { command?: string; result?: string } = {},
) {
  return backendApi.get<{
    success: boolean
    data: BotControlAuditItem[]
    limit: number
    offset: number
    total: number
  }>(`${AUDIT_BASE}/bot-control-audit`, {
    query: {
      session_id: query.session_id ?? '',
      person_id: query.person_id ?? '',
      command: query.command ?? '',
      result: query.result ?? '',
      reveal: query.reveal ?? false,
      limit: query.limit ?? 100,
      offset: query.offset ?? 0,
    },
    cache: 'no-store',
    errorMessage: '读取控制审计失败',
  })
}

export async function getKamiProfile(): Promise<KamiProfileItem> {
  return backendApi.get<KamiProfileItem>(`${KAMI_BASE}/profile`, {
    cache: 'no-store',
    errorMessage: '读取 Kami 配置失败',
  })
}

export async function updateKamiProfile(input: {
  persona_profile_id?: string | null
  inherit_parent_persona?: boolean
  inherit_parent_tools?: boolean
  inherit_parent_plugins?: boolean
  enabled?: boolean
  expected_revision?: number
}): Promise<KamiProfileItem> {
  return backendApi.patch<KamiProfileItem>(`${KAMI_BASE}/profile`, {
    body: input,
    errorMessage: '更新 Kami 配置失败',
  })
}

export async function listKamiSessions(status?: string): Promise<KamiSessionItem[]> {
  const response = await backendApi.get<{ success: boolean; data: KamiSessionItem[]; boot_id: string }>(
    `${KAMI_BASE}/sessions`,
    {
      query: { status: status ?? '', limit: 200 },
      cache: 'no-store',
      errorMessage: '读取 Kami 会话失败',
    },
  )
  return response.data
}

export async function revokeKamiSession(stateId: string): Promise<boolean> {
  const response = await backendApi.post<{ success: boolean; removed: boolean }>(
    `${KAMI_BASE}/sessions/${encodeURIComponent(stateId)}/revoke`,
    { errorMessage: '撤销 Kami 会话失败' },
  )
  return response.removed
}
