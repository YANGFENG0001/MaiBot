import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import {
  createPersona,
  deletePersona,
  getPersonas,
  personaInUse,
  updatePersona,
  type PersonaItem,
} from '../personas-api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const patchMock = vi.mocked(backendApi.patch)
const deleteMock = vi.mocked(backendApi.delete)

function makePersona(overrides: Partial<PersonaItem> = {}): PersonaItem {
  return {
    id: 'persona-profile-abc123',
    name: '测试人设',
    description: '',
    nickname: '小测',
    alias_names: ['测试'],
    personality: '温和',
    behavior_style: '',
    reply_style: '',
    group_chat_prompt: '',
    private_chat_prompt: '',
    multiple_reply_style: '',
    emotion_trait: '',
    usage: { bot_profiles: [], workspaces: [] },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  patchMock.mockReset()
  deleteMock.mockReset()
})

describe('getPersonas', () => {
  it('以 no-store 读取人设列表并返回 data', async () => {
    const personas = [makePersona(), makePersona({ id: 'persona-profile-def456' })]
    getMock.mockResolvedValue({ success: true, data: personas })

    await expect(getPersonas()).resolves.toBe(personas)
    expect(getMock).toHaveBeenCalledWith('/api/webui/personas', {
      cache: 'no-store',
      errorMessage: '读取人设列表失败',
    })
  })
})

describe('createPersona', () => {
  it('POST 到人设集合并把入参原样作为 body', async () => {
    const created = makePersona({ name: '高冷人设' })
    postMock.mockResolvedValue({ success: true, data: created })

    const input = { name: '高冷人设', personality: '高冷', alias_names: ['甲', '乙'] }
    await expect(createPersona(input)).resolves.toBe(created)
    expect(postMock).toHaveBeenCalledWith('/api/webui/personas', {
      body: input,
      errorMessage: '创建人设失败',
    })
  })
})

describe('updatePersona', () => {
  it('PATCH 到编码后的人设 ID', async () => {
    const updated = makePersona({ personality: '冷静' })
    patchMock.mockResolvedValue({ success: true, data: updated })

    await expect(updatePersona('persona-profile-abc123', { personality: '冷静' })).resolves.toBe(updated)
    expect(patchMock).toHaveBeenCalledWith('/api/webui/personas/persona-profile-abc123', {
      body: { personality: '冷静' },
      errorMessage: '更新人设失败',
    })
  })

  it('对 ID 做 URL 编码，避免特殊字符破坏路径', async () => {
    patchMock.mockResolvedValue({ success: true, data: makePersona() })
    await updatePersona('persona/with space', { nickname: 'x' })
    expect(patchMock).toHaveBeenCalledWith('/api/webui/personas/persona%2Fwith%20space', expect.anything())
  })
})

describe('deletePersona', () => {
  it('DELETE 后返回 removed 布尔值', async () => {
    deleteMock.mockResolvedValue({ success: true, removed: true })
    await expect(deletePersona('persona-profile-abc123')).resolves.toBe(true)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/personas/persona-profile-abc123', {
      errorMessage: '删除人设失败',
    })
  })
})

describe('personaInUse', () => {
  it('被 BotProfile 引用时为 true', () => {
    expect(personaInUse(makePersona({ usage: { bot_profiles: ['bot-profile-a'], workspaces: [] } }))).toBe(true)
  })

  it('被 Workspace 引用时为 true', () => {
    expect(personaInUse(makePersona({ usage: { bot_profiles: [], workspaces: ['workspace-a'] } }))).toBe(true)
  })

  it('没有任何引用时为 false', () => {
    expect(personaInUse(makePersona())).toBe(false)
  })
})
