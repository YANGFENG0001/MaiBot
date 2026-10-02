/**
 * W03 / 工作区管理控制台：七个分区、加载态、下拉层级与语言切换。
 *
 * 这里刻意只 mock API 客户端，让真实面板渲染，从而能断言下拉弹层的层级与不透明度。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { WorkspacesPage } from '../workspaces'

vi.mock('@/lib/workspaces-api', () => ({
  getWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
  getAvailableWorkspaceChats: vi.fn(),
  assignWorkspaceChats: vi.fn(),
  createMemorySpace: vi.fn(),
  updateMemorySpace: vi.fn(),
  getMemorySpacePartitions: vi.fn(),
  getMemorySpaceBotAccess: vi.fn(),
  getMemorySpaceAcl: vi.fn(),
  setMemorySpaceAcl: vi.fn(),
  migrateLegacyMemoryGroups: vi.fn(),
}))

vi.mock('@/lib/bot-profiles-api', () => ({
  getBotProfiles: vi.fn(),
  updateBotProfile: vi.fn(),
  setBotProfileTool: vi.fn(),
  removeBotProfileTool: vi.fn(),
  setBotProfilePlugin: vi.fn(),
  removeBotProfilePlugin: vi.fn(),
  setBotProfileMemoryRule: vi.fn(),
  removeBotProfileMemoryRule: vi.fn(),
}))

vi.mock('@/lib/memory-permissions-api', () => ({
  getPermissionGroups: vi.fn(),
  getPermissionGroup: vi.fn(),
  createPermissionGroup: vi.fn(),
  updatePermissionGroup: vi.fn(),
  deletePermissionGroup: vi.fn(),
  addGroupMember: vi.fn(),
  removeGroupMember: vi.fn(),
  addGroupContext: vi.fn(),
  removeGroupContext: vi.fn(),
  setGroupCapability: vi.fn(),
  removeGroupCapability: vi.fn(),
  addGroupRule: vi.fn(),
  deleteGroupRule: vi.fn(),
  setGroupBotRule: vi.fn(),
  removeGroupBotRule: vi.fn(),
  listPersonOptions: vi.fn(),
  simulateAccess: vi.fn(),
}))

vi.mock('@/lib/memory-audit-api', () => ({
  getMemoryAccessAudit: vi.fn(),
  getBotControlAudit: vi.fn(),
  getKamiProfile: vi.fn(),
  updateKamiProfile: vi.fn(),
  listKamiSessions: vi.fn(),
  revokeKamiSession: vi.fn(),
}))

vi.mock('@/lib/memory-transfers-api', () => ({
  listTransfers: vi.fn(),
  getTransfer: vi.fn(),
  createTransfer: vi.fn(),
  planTransfer: vi.fn(),
  executeTransfer: vi.fn(),
  retryTransfer: vi.fn(),
  reconcileTransfer: vi.fn(),
  cancelTransfer: vi.fn(),
  approveTransfer: vi.fn(),
  rejectTransfer: vi.fn(),
  revokeTransferApproval: vi.fn(),
}))

import * as botProfilesApi from '@/lib/bot-profiles-api'
import * as memoryAuditApi from '@/lib/memory-audit-api'
import * as memoryPermissionsApi from '@/lib/memory-permissions-api'
import * as memoryTransfersApi from '@/lib/memory-transfers-api'
import * as workspacesApi from '@/lib/workspaces-api'

const SPACE = {
  id: 'memory-space-public',
  name: '公共记忆库',
  description: '',
  space_type: 'public' as const,
  enabled: true,
  strict_isolation: false,
  policy_revision: 1,
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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspacesPage />
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
  vi.mocked(workspacesApi.getAvailableWorkspaceChats).mockResolvedValue([])
  vi.mocked(workspacesApi.getMemorySpacePartitions).mockResolvedValue({
    success: true,
    memory_space_id: SPACE.id,
    memory_space_name: SPACE.name,
    strict_isolation: false,
    data: [],
  })
  vi.mocked(workspacesApi.getMemorySpaceBotAccess).mockResolvedValue([])
  vi.mocked(workspacesApi.getMemorySpaceAcl).mockResolvedValue([])
  vi.mocked(botProfilesApi.getBotProfiles).mockResolvedValue([])
  vi.mocked(memoryPermissionsApi.getPermissionGroups).mockResolvedValue({
    success: true,
    // 详情区（含各下拉框）只在选中权限组后渲染，因此这里必须返回至少一个组。
    data: [
      {
        id: 'group-1',
        name: '管理员组',
        description: '',
        enabled: true,
        priority: 10,
        memory_scope_mode: 'override',
        is_manager_mode: false,
        policy_revision: 1,
        counts: { members: 0, contexts: 0, capabilities: 0, rules: 0, bot_rules: 0 },
        created_at: '2026-01-01T00:00:00',
        updated_at: '2026-01-02T00:00:00',
      },
    ],
    capabilities: [],
    scope_types: ['global', 'workspace', 'session', 'channel'],
    space_selectors: ['current', 'public', 'specific', 'all_normal'],
    partition_types: ['any', 'shared', 'person', 'conversation'],
    partition_selectors: ['any', 'self', 'current', 'specific'],
    bot_selectors: ['public', 'current_group', 'kami', 'specific'],
    manager_preset_capabilities: ['bot.switch.kami', 'memory.read.force_all'],
  })
  // 后端详情响应是「data 承载组本身 + 成员/上下文/能力/规则平铺在同级」的扁平结构。
  vi.mocked(memoryPermissionsApi.getPermissionGroup).mockResolvedValue({
    success: true,
    data: {
      id: 'group-1',
      name: '管理员组',
      description: '',
      enabled: true,
      priority: 10,
      memory_scope_mode: 'override',
      is_manager_mode: false,
      policy_revision: 1,
      counts: { members: 0, contexts: 0, capabilities: 0, rules: 0, bot_rules: 0 },
      created_at: '2026-01-01T00:00:00',
      updated_at: '2026-01-02T00:00:00',
    },
    members: [],
    contexts: [],
    capabilities: [],
    rules: [],
    bot_rules: [],
  })
  vi.mocked(memoryPermissionsApi.listPersonOptions).mockResolvedValue([])
  vi.mocked(memoryAuditApi.getMemoryAccessAudit).mockResolvedValue({
    success: true,
    data: [],
    limit: 100,
    offset: 0,
    total: 0,
  })
  vi.mocked(memoryAuditApi.getBotControlAudit).mockResolvedValue({
    success: true,
    data: [],
    limit: 100,
    offset: 0,
    total: 0,
  })
  vi.mocked(memoryAuditApi.getKamiProfile).mockResolvedValue({
    id: 'bot-profile-kami',
    name: 'Kami',
    profile_type: 'kami',
    enabled: true,
    home_memory_space_id: 'memory-space-kami',
    persona_profile_id: null,
    inherit_parent_persona: false,
    inherit_parent_tools: false,
    inherit_parent_plugins: false,
    is_system: true,
    dangerous: true,
    default_ttl_seconds: 900,
    max_ttl_seconds: 86400,
    policy_revision: 1,
  })
  vi.mocked(memoryAuditApi.listKamiSessions).mockResolvedValue([])
  // listTransfers 直接返回数组（不是 {success, data} 包装）。
  vi.mocked(memoryTransfersApi.listTransfers).mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  localStorage.removeItem('maibot-locale')
})

describe('WorkspacesPage', () => {
  it('渲染七个分区标签并默认展示聊天分组', async () => {
    renderPage()

    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      '聊天分组',
      'Bot 配置',
      '记忆库',
      '权限组',
      'Kami 管理',
      '同步与导入',
      '审计与模拟',
    ])
    expect(await screen.findByRole('tab', { name: '聊天分组', selected: true })).toBeInTheDocument()
  })

  it('切换到记忆库分区后渲染记忆空间面板', async () => {
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('tab', { name: '记忆库' }))

    // 「逻辑分区」只在记忆空间面板里出现，可用来确认面板已挂载。
    expect(await screen.findByText('逻辑分区')).toBeInTheDocument()
    await waitFor(() => {
      expect(workspacesApi.getMemorySpacePartitions).toHaveBeenCalled()
    })
  })

  it('W03 下拉框弹层位于顶层且使用不透明背景', async () => {
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('tab', { name: '权限组' }))
    // 权限组面板在列表返回前只渲染加载态，需等它进入正式布局。
    await screen.findByText('成员')

    const triggers = await screen.findAllByRole('combobox')
    await user.click(triggers[0])

    const listbox = await screen.findByRole('listbox')
    // Radix 通过 Portal 把弹层挂到 body 下，保证不被卡片裁剪。
    expect(listbox.closest('[data-radix-popper-content-wrapper]')).not.toBeNull()
    const content = listbox.closest('[data-radix-popper-content-wrapper]')?.firstElementChild as HTMLElement
    expect(content.className).toContain('z-[100]')
    expect(content.className).toContain('bg-popover')
  })

  it('W03 下拉框可通过键盘操作并选中选项', async () => {
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('tab', { name: '审计与模拟' }))
    const triggers = await screen.findAllByRole('combobox')

    triggers[0].focus()
    await user.keyboard('{Enter}')
    const options = await screen.findAllByRole('option')
    expect(options.length).toBeGreaterThan(0)

    await user.keyboard('{ArrowDown}{Enter}')
    await waitFor(() => {
      expect(screen.queryByRole('listbox')).toBeNull()
    })
  })

  it('切换语言后分区标签文本随之变化', async () => {
    renderPage()
    expect(await screen.findByRole('tab', { name: '聊天分组' })).toBeInTheDocument()

    await i18n.changeLanguage('en')

    expect(await screen.findByRole('tab', { name: 'Chat groups' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '聊天分组' })).toBeNull()
  })

  it('加载期间展示加载态文案而不是空表格', async () => {
    let resolveProfiles: (value: never[]) => void = () => {}
    vi.mocked(botProfilesApi.getBotProfiles).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveProfiles = resolve as (value: never[]) => void
        }),
    )
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('tab', { name: 'Bot 配置' }))
    expect(await screen.findByText(/正在加载/)).toBeInTheDocument()

    resolveProfiles([])
    await waitFor(() => {
      expect(screen.queryByText(/正在加载/)).toBeNull()
    })
  })
})
