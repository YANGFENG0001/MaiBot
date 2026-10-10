/**
 * 「按 Bot 覆盖人设」面板。
 *
 * 只 mock API 客户端，让真实组件渲染，从而能验证：
 * 绑定关系、字段的「覆盖 / 继承」判定、保存与删除的守卫，以及会话路由列表。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import type { BotProfileItem } from '@/lib/bot-profiles-api'
import type { PersonaItem } from '@/lib/personas-api'

import { BotPersonaOverrides } from '../BotPersonaOverrides'

vi.mock('@/lib/bot-profiles-api', () => ({
  getBotProfiles: vi.fn(),
  createBotProfile: vi.fn(),
  deleteBotProfile: vi.fn(),
  updateBotProfile: vi.fn(),
  getBotRoutes: vi.fn(),
  setBotRoute: vi.fn(),
  resetBotRoute: vi.fn(),
  setBotProfileTool: vi.fn(),
  removeBotProfileTool: vi.fn(),
  setBotProfilePlugin: vi.fn(),
  removeBotProfilePlugin: vi.fn(),
  setBotProfileMemoryRule: vi.fn(),
  removeBotProfileMemoryRule: vi.fn(),
}))

vi.mock('@/lib/personas-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/personas-api')>()
  return {
    ...actual,
    getPersonas: vi.fn(),
    createPersona: vi.fn(),
    updatePersona: vi.fn(),
    deletePersona: vi.fn(),
  }
})

vi.mock('@/lib/workspaces-api', () => ({
  getWorkspaces: vi.fn(),
}))

const botProfilesApi = await import('@/lib/bot-profiles-api')
const personasApi = await import('@/lib/personas-api')
const workspacesApi = await import('@/lib/workspaces-api')

const SPACE = {
  id: 'memory-space-public',
  name: '公共记忆库',
  description: '',
  space_type: 'public' as const,
  enabled: true,
  strict_isolation: false,
  policy_revision: 1,
}

function makeProfile(overrides: Partial<BotProfileItem> = {}): BotProfileItem {
  return {
    id: 'bot-profile-public',
    name: '公共 Bot',
    profile_type: 'public',
    parent_profile_id: null,
    persona_profile_id: null,
    home_memory_space_id: SPACE.id,
    home_memory_space_name: SPACE.name,
    inherit_parent_persona: true,
    inherit_parent_tools: true,
    inherit_parent_plugins: true,
    enabled: true,
    is_system: true,
    policy_revision: 1,
    lineage: [{ id: 'bot-profile-public', name: '公共 Bot', profile_type: 'public' }],
    tool_policies: [],
    plugin_policies: [],
    memory_rules: [],
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-02T00:00:00',
    ...overrides,
  }
}

function makePersona(overrides: Partial<PersonaItem> = {}): PersonaItem {
  return {
    id: 'persona-profile-kami',
    name: 'Kami 管理人设',
    description: '',
    nickname: 'Kami',
    alias_names: [],
    personality: '审慎、准确',
    behavior_style: '',
    reply_style: '简洁',
    group_chat_prompt: '',
    private_chat_prompt: '',
    multiple_reply_style: '',
    emotion_trait: '',
    usage: { bot_profiles: [], workspaces: [] },
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-02T00:00:00',
    ...overrides,
  }
}

// jsdom 未实现 Radix Select 依赖的指针捕获 / 滚动 API，按仓库既有做法补最小实现。
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <BotPersonaOverrides />
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  localStorage.setItem('maibot-locale', 'zh')
  await i18n.changeLanguage('zh')

  vi.mocked(workspacesApi.getWorkspaces).mockResolvedValue({
    success: true,
    data: [],
    memory_spaces: [SPACE],
  })
  vi.mocked(botProfilesApi.getBotRoutes).mockResolvedValue([])
  vi.mocked(personasApi.getPersonas).mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('BotPersonaOverrides', () => {
  it('默认选中第一个 Bot 并展示它的详情', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile(),
      makeProfile({ id: 'bot-profile-group1', name: '群聊 Bot', profile_type: 'group', is_system: false }),
    ])

    renderPanel()

    // 「公共 Bot」在列表项、详情名称与继承链里各出现一次，所以只能用 findAllByText。
    expect((await screen.findAllByText('公共 Bot')).length).toBeGreaterThan(0)
    // 「群聊 Bot」只出现在左栏列表里（未被选中），是单元素。
    expect(screen.getByText('群聊 Bot')).toBeInTheDocument()
    // 默认选中列表里的第一个 Bot。
    expect(screen.getByRole('button', { name: /公共 Bot/ })).toHaveAttribute('aria-pressed', 'true')
    // 详情区展示它的归属记忆库。
    expect(await screen.findByText('公共记忆库')).toBeInTheDocument()
  })

  it('未绑定人设时提示将使用全局配置', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile()])

    renderPanel()

    expect(
      await screen.findByText('「公共 Bot」当前未绑定人设，将使用全局人设配置。'),
    ).toBeInTheDocument()
  })

  it('绑定了人设时渲染字段，并按是否留空区分「已覆盖 / 继承全局」', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile({ persona_profile_id: 'persona-profile-kami' }),
    ])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([makePersona()])

    renderPanel()

    expect(await screen.findByDisplayValue('审慎、准确')).toBeInTheDocument()
    // personality 与 reply_style 有值，behavior_style 为空。
    expect(screen.getAllByText('已覆盖')).toHaveLength(3)
    expect(screen.getAllByText('继承全局').length).toBeGreaterThan(0)
  })

  it('没有改动时保存按钮禁用，改动后启用并调用 updatePersona', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile({ persona_profile_id: 'persona-profile-kami' }),
    ])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([makePersona()])
    vi.mocked(personasApi.updatePersona).mockResolvedValue(makePersona())

    renderPanel()

    const saveButton = await screen.findByRole('button', { name: '保存人设' })
    expect(saveButton).toBeDisabled()

    // 昵称原本是「Kami」，先清空再输入，避免 userEvent 追加式输入。
    const nicknameInput = screen.getByLabelText('昵称')
    await userEvent.clear(nicknameInput)
    await userEvent.type(nicknameInput, '改了')
    await waitFor(() => expect(saveButton).toBeEnabled())

    await userEvent.click(saveButton)

    await waitFor(() =>
      expect(personasApi.updatePersona).toHaveBeenCalledWith(
        'persona-profile-kami',
        expect.objectContaining({ nickname: '改了', personality: '审慎、准确' }),
      ),
    )
  })

  it('未绑定人设时不显示删除人设按钮，但非系统 Bot 仍可删除', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile({ id: 'bot-profile-group1', name: '群聊 Bot', profile_type: 'group', is_system: false }),
    ])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([makePersona()])

    renderPanel()

    await screen.findAllByText('群聊 Bot')
    // 未绑定人设：没有删除人设的按钮（该按钮只在 boundPersona 存在时渲染）；
    // 非系统 Bot 应该有删除 Bot 的按钮。
    expect(screen.queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除 Bot' })).toBeInTheDocument()
  })

  it('系统内置 Bot 不显示删除 Bot 按钮', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile({ is_system: true })])

    renderPanel()

    await screen.findAllByText('公共 Bot')
    expect(screen.queryByRole('button', { name: '删除 Bot' })).not.toBeInTheDocument()
  })

  it('人设仍被引用时删除按钮禁用', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile({ persona_profile_id: 'persona-profile-kami' }),
    ])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([
      makePersona({ usage: { bot_profiles: ['bot-profile-public'], workspaces: [] } }),
    ])

    renderPanel()

    expect(await screen.findByRole('button', { name: '删除' })).toBeDisabled()
  })

  it('人设未被引用时可删除并调用 deletePersona', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile({ persona_profile_id: 'persona-profile-kami' }),
    ])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([makePersona()])
    vi.mocked(personasApi.deletePersona).mockResolvedValue(true)

    renderPanel()

    const deleteButton = await screen.findByRole('button', { name: '删除' })
    expect(deleteButton).toBeEnabled()
    await userEvent.click(deleteButton)

    await waitFor(() => expect(personasApi.deletePersona).toHaveBeenCalledWith('persona-profile-kami'))
  })

  it('会话路由列表渲染 Bot 名称，并可恢复默认', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile()])
    vi.mocked(botProfilesApi.getBotRoutes).mockResolvedValue([
      {
        session_id: 'session-a',
        active_bot_profile_id: 'bot-profile-group1',
        active_bot_profile_name: '群聊 Bot',
        route_mode: 'specific',
        changed_by_person_id: 'webui',
        policy_revision: 1,
        updated_at: '2026-01-02T00:00:00',
      },
    ])
    vi.mocked(botProfilesApi.resetBotRoute).mockResolvedValue(true)

    renderPanel()

    expect(await screen.findByText('session-a')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '恢复默认 Bot' }))

    await waitFor(() => expect(botProfilesApi.resetBotRoute).toHaveBeenCalledWith('session-a'))
  })

  it('没有会话级路由时显示空态', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile()])

    renderPanel()

    expect(await screen.findByText('暂无会话级路由，全部使用子系统默认 Bot。')).toBeInTheDocument()
  })

  it('没有可配置的 Bot 时给出引导', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([])

    renderPanel()

    expect(await screen.findByText('还没有可配置的 Bot')).toBeInTheDocument()
  })

  it('新建 Bot 对话框在未填名称或未选记忆空间时禁用提交', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile()])

    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: '新建 Bot' }))

    // 用 within 限定在对话框内，否则会误取到外层同名触发器。
    const dialog = await screen.findByRole('dialog')
    const createButton = within(dialog).getByRole('button', { name: '创建' })
    expect(createButton).toBeDisabled()

    // 只填名称、未选主记忆空间，仍然禁用。
    await userEvent.type(within(dialog).getByLabelText('Bot 名称'), '共享组 Bot')
    expect(createButton).toBeDisabled()
  })

  it('切换 Bot 时详情与草稿跟随切换', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile({ persona_profile_id: 'persona-profile-kami' }),
      makeProfile({ id: 'bot-profile-group1', name: '群聊 Bot', profile_type: 'group', is_system: false }),
    ])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([makePersona()])

    renderPanel()

    // 第一个 Bot 绑了人设，所以有人设编辑器。
    expect(await screen.findByRole('button', { name: '保存人设' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /群聊 Bot/ }))

    // 切到未绑定人设的 Bot：编辑器消失，改为提示沿用全局配置。
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '保存人设' })).not.toBeInTheDocument(),
    )
    expect(
      screen.getByText('「群聊 Bot」当前未绑定人设，将使用全局人设配置。'),
    ).toBeInTheDocument()
  })

  it('在详情里选择人设会调用 updateBotProfile 完成绑定', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile()])
    vi.mocked(personasApi.getPersonas).mockResolvedValue([makePersona()])
    vi.mocked(botProfilesApi.updateBotProfile).mockResolvedValue(makeProfile())

    renderPanel()

    const detailCard = (await screen.findByText('Bot 详情')).closest(
      '[data-dashboard-card="true"]',
    ) as HTMLElement
    await userEvent.click(within(detailCard).getByRole('combobox'))
    await userEvent.click(await screen.findByRole('option', { name: 'Kami 管理人设' }))

    await waitFor(() =>
      expect(botProfilesApi.updateBotProfile).toHaveBeenCalledWith('bot-profile-public', {
        persona_profile_id: 'persona-profile-kami',
        expected_revision: 1,
      }),
    )
  })

  it('新建人设后自动绑定到当前 Bot', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([makeProfile()])
    vi.mocked(botProfilesApi.updateBotProfile).mockResolvedValue(makeProfile())
    const created = makePersona({ id: 'persona-profile-new', name: '新人格' })
    vi.mocked(personasApi.createPersona).mockResolvedValue(created)

    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: '新建人设' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('人设名称'), '新人格')
    await userEvent.click(within(dialog).getByRole('button', { name: '创建' }))

    await waitFor(() => expect(personasApi.createPersona).toHaveBeenCalled())
    // 创建后立即绑定到当前选中的 Bot，省掉一次手动选择。
    await waitFor(() =>
      expect(botProfilesApi.updateBotProfile).toHaveBeenCalledWith('bot-profile-public', {
        persona_profile_id: 'persona-profile-new',
        expected_revision: 1,
      }),
    )
  })

  it('填写会话 ID 并选择 Bot 后应用路由', async () => {
    vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([
      makeProfile(),
      makeProfile({ id: 'bot-profile-group1', name: '群聊 Bot', profile_type: 'group', is_system: false }),
    ])
    vi.mocked(botProfilesApi.setBotRoute).mockResolvedValue({
      session_id: 'session-b',
      active_bot_profile_id: 'bot-profile-group1',
      active_bot_profile_name: '群聊 Bot',
      route_mode: 'specific',
      changed_by_person_id: 'webui',
      policy_revision: 1,
      updated_at: '2026-01-02T00:00:00',
    })

    renderPanel()

    await userEvent.type(await screen.findByLabelText('会话 ID'), 'session-b')

    const routeCard = screen.getByText('会话 Bot 路由').closest(
      '[data-dashboard-card="true"]',
    ) as HTMLElement
    await userEvent.click(within(routeCard).getByRole('combobox'))
    await userEvent.click(await screen.findByRole('option', { name: '群聊 Bot' }))
    await userEvent.click(screen.getByRole('button', { name: '应用路由' }))

    await waitFor(() =>
      expect(botProfilesApi.setBotRoute).toHaveBeenCalledWith('session-b', 'bot-profile-group1'),
    )
  })
})
