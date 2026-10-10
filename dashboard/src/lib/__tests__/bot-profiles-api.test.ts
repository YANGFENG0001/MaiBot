import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import {
  createBotProfile,
  deleteBotProfile,
  getBotRoutes,
  resetBotRoute,
  setBotRoute,
  type BotProfileItem,
  type BotRouteStateItem,
} from '../bot-profiles-api'

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
const putMock = vi.mocked(backendApi.put)
const deleteMock = vi.mocked(backendApi.delete)

function makeProfile(overrides: Partial<BotProfileItem> = {}): BotProfileItem {
  return {
    id: 'bot-profile-group1',
    name: '群聊 Bot',
    profile_type: 'group',
    parent_profile_id: 'bot-profile-public',
    persona_profile_id: null,
    home_memory_space_id: 'memory-space-public',
    home_memory_space_name: '公共记忆库',
    inherit_parent_persona: true,
    inherit_parent_tools: true,
    inherit_parent_plugins: true,
    enabled: true,
    is_system: false,
    policy_revision: 1,
    lineage: [],
    tool_policies: [],
    plugin_policies: [],
    memory_rules: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

function makeRoute(overrides: Partial<BotRouteStateItem> = {}): BotRouteStateItem {
  return {
    session_id: 'session-a',
    active_bot_profile_id: 'bot-profile-group1',
    active_bot_profile_name: '群聊 Bot',
    route_mode: 'specific',
    changed_by_person_id: 'webui',
    policy_revision: 1,
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  putMock.mockReset()
  deleteMock.mockReset()
})

describe('createBotProfile', () => {
  it('POST 到 bot-profiles 并返回新建的 profile', async () => {
    const created = makeProfile()
    postMock.mockResolvedValue({ success: true, data: created })

    const input = { name: '群聊 Bot', home_memory_space_id: 'memory-space-public' }
    await expect(createBotProfile(input)).resolves.toBe(created)
    expect(postMock).toHaveBeenCalledWith('/api/webui/bot-profiles', {
      body: input,
      errorMessage: '创建 Bot 失败',
    })
  })
})

describe('deleteBotProfile', () => {
  it('DELETE 后返回 removed 布尔值', async () => {
    deleteMock.mockResolvedValue({ success: true, removed: true })
    await expect(deleteBotProfile('bot-profile-group1')).resolves.toBe(true)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/bot-profiles/bot-profile-group1', {
      errorMessage: '删除 Bot 失败',
    })
  })
})

describe('getBotRoutes', () => {
  it('以 no-store 读取会话路由列表', async () => {
    const routes = [makeRoute()]
    getMock.mockResolvedValue({ success: true, data: routes })

    await expect(getBotRoutes()).resolves.toBe(routes)
    expect(getMock).toHaveBeenCalledWith('/api/webui/bot-profiles/routes', {
      cache: 'no-store',
      errorMessage: '读取会话 Bot 路由失败',
    })
  })
})

describe('setBotRoute', () => {
  it('PUT 到会话路由路径，默认 route_mode 为 specific', async () => {
    const route = makeRoute()
    putMock.mockResolvedValue(route)

    await expect(setBotRoute('session-a', 'bot-profile-group1')).resolves.toBe(route)
    expect(putMock).toHaveBeenCalledWith('/api/webui/bot-profiles/routes/session-a', {
      body: { active_bot_profile_id: 'bot-profile-group1', route_mode: 'specific' },
      errorMessage: '设置会话 Bot 失败',
    })
  })

  it('对 session_id 做 URL 编码', async () => {
    putMock.mockResolvedValue(makeRoute())
    await setBotRoute('group/123 456', 'bot-profile-group1')
    expect(putMock).toHaveBeenCalledWith(
      '/api/webui/bot-profiles/routes/group%2F123%20456',
      expect.anything(),
    )
  })
})

describe('resetBotRoute', () => {
  it('DELETE 后返回 removed 布尔值', async () => {
    deleteMock.mockResolvedValue({ success: true, removed: false })
    await expect(resetBotRoute('session-a')).resolves.toBe(false)
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/bot-profiles/routes/session-a', {
      errorMessage: '恢复默认 Bot 失败',
    })
  })
})
