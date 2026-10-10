import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/personas'

/** 引用该人设的对象；删除前用它提示「还有谁在用」。 */
export interface PersonaUsage {
  bot_profiles: string[]
  workspaces: string[]
}

export interface PersonaItem {
  id: string
  name: string
  description: string
  nickname: string
  alias_names: string[]
  personality: string
  behavior_style: string
  reply_style: string
  group_chat_prompt: string
  private_chat_prompt: string
  multiple_reply_style: string
  emotion_trait: string
  usage: PersonaUsage
  created_at: string
  updated_at: string
}

/** 可编辑的文本字段，与后端 `PERSONA_TEXT_FIELDS` 一一对应。 */
export const PERSONA_TEXT_FIELDS = [
  'description',
  'nickname',
  'personality',
  'behavior_style',
  'reply_style',
  'group_chat_prompt',
  'private_chat_prompt',
  'multiple_reply_style',
  'emotion_trait',
] as const

export type PersonaTextField = (typeof PERSONA_TEXT_FIELDS)[number]

export interface PersonaCreateInput {
  name: string
  alias_names?: string[] | string
  description?: string
  nickname?: string
  personality?: string
  behavior_style?: string
  reply_style?: string
  group_chat_prompt?: string
  private_chat_prompt?: string
  multiple_reply_style?: string
  emotion_trait?: string
}

export type PersonaUpdateInput = Partial<Omit<PersonaCreateInput, 'name'>> & { name?: string }

export async function getPersonas(): Promise<PersonaItem[]> {
  const response = await backendApi.get<{ success: boolean; data: PersonaItem[] }>(API_BASE, {
    cache: 'no-store',
    errorMessage: '读取人设列表失败',
  })
  return response.data
}

export async function createPersona(input: PersonaCreateInput): Promise<PersonaItem> {
  const response = await backendApi.post<{ success: boolean; data: PersonaItem }>(API_BASE, {
    body: input,
    errorMessage: '创建人设失败',
  })
  return response.data
}

export async function updatePersona(personaId: string, input: PersonaUpdateInput): Promise<PersonaItem> {
  const response = await backendApi.patch<{ success: boolean; data: PersonaItem }>(
    `${API_BASE}/${encodeURIComponent(personaId)}`,
    { body: input, errorMessage: '更新人设失败' },
  )
  return response.data
}

export async function deletePersona(personaId: string): Promise<boolean> {
  const response = await backendApi.delete<{ success: boolean; removed: boolean }>(
    `${API_BASE}/${encodeURIComponent(personaId)}`,
    { errorMessage: '删除人设失败' },
  )
  return response.removed
}

/** 判断某个人设是否仍被引用（被引用时不允许删除）。 */
export function personaInUse(persona: PersonaItem): boolean {
  return persona.usage.bot_profiles.length > 0 || persona.usage.workspaces.length > 0
}
