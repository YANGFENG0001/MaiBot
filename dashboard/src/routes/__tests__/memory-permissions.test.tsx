/**
 * 权限组面板：加载态、空状态、保存成功、W04 409 乐观锁冲突、403 权限拒绝。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { PermissionGroupsPanel } from '@/components/workspaces/permission-groups-panel'
import { ApiError } from '@/lib/http'
import * as api from '@/lib/memory-permissions-api'

import type { PermissionGroupItem } from '@/lib/memory-permissions-api'

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

const CATALOG = {
  success: true as const,
  data: [] as PermissionGroupItem[],
  capabilities: ['memory.read.cross_space', 'memory.read.force_all'],
  scope_types: ['global', 'workspace', 'session', 'channel'],
  space_selectors: ['current', 'public', 'specific', 'all_normal'],
  partition_types: ['any', 'shared', 'person', 'conversation'],
  partition_selectors: ['any', 'self', 'current', 'specific'],
  bot_selectors: ['public', 'current_group', 'kami', 'specific'],
  manager_preset_capabilities: ['bot.switch.kami', 'memory.read.force_all'],
}

const GROUP = {
  id: 'group-1',
  name: '管理员组',
  description: '管理用途',
  enabled: true,
  priority: 10,
  memory_scope_mode: 'override' as const,
  is_manager_mode: true,
  policy_revision: 3,
  counts: { members: 1, contexts: 1, capabilities: 2, rules: 0, bot_rules: 0 },
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-02T00:00:00',
}

// 后端详情响应是「data 承载组本身 + 成员/上下文/能力/规则平铺在同级」的扁平结构。
const DETAIL = {
  success: true as const,
  data: GROUP,
  members: [],
  contexts: [],
  capabilities: [],
  rules: [],
  bot_rules: [],
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <PermissionGroupsPanel />
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  localStorage.setItem('maibot-locale', 'zh')
  await i18n.changeLanguage('zh')
  // 默认返回一个权限组，便于覆盖详情区交互；空状态用例单独覆盖。
  vi.mocked(api.getPermissionGroups).mockResolvedValue({ ...CATALOG, data: [GROUP] })
  vi.mocked(api.getPermissionGroup).mockResolvedValue(DETAIL)
  vi.mocked(api.listPersonOptions).mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  localStorage.removeItem('maibot-locale')
})

describe('PermissionGroupsPanel', () => {
  it('加载期间展示加载态', async () => {
    vi.mocked(api.getPermissionGroups).mockImplementation(
      () => new Promise(() => {}) as ReturnType<typeof api.getPermissionGroups>,
    )
    renderPanel()
    expect(await screen.findByText(/正在加载/)).toBeInTheDocument()
  })

  it('没有权限组时展示空状态', async () => {
    vi.mocked(api.getPermissionGroups).mockResolvedValue(CATALOG)
    renderPanel()
    expect(await screen.findByText(/暂无权限组/)).toBeInTheDocument()
  })

  it('创建权限组成功后刷新列表并选中新组', async () => {
    const user = userEvent.setup()
    // 列表初始为空，创建成功后重新拉取才返回新组——用来验证「创建后确实刷新」。
    let groups: PermissionGroupItem[] = []
    vi.mocked(api.getPermissionGroups).mockImplementation(async () => ({ ...CATALOG, data: groups }))
    renderPanel()

    await screen.findByText(/暂无权限组/)
    vi.mocked(api.createPermissionGroup).mockImplementation(async (input) => {
      groups = [{ ...GROUP, name: input.name }]
      return groups[0]
    })

    await user.click(screen.getByRole('button', { name: /新建权限组/ }))
    await user.type(await screen.findByLabelText('名称'), '管理员组')
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(api.createPermissionGroup).toHaveBeenCalledWith(
        expect.objectContaining({ name: '管理员组' }),
      )
    })
    expect((await screen.findAllByText('管理员组')).length).toBeGreaterThan(0)
  })

  it('W04 409 乐观锁冲突时提示配置已被更新且不静默覆盖', async () => {
    const user = userEvent.setup()
    renderPanel()

    await screen.findAllByText('管理员组')
    await screen.findByText('成员')
    vi.mocked(api.updatePermissionGroup).mockRejectedValue(
      new ApiError('配置已被其他管理员更新，请刷新后重试', { status: 409 }),
    )

    await user.click(screen.getAllByRole('switch')[0])

    expect(await screen.findByText(/配置已被其他管理员更新，请刷新后重试/)).toBeInTheDocument()
    // 冲突后仍提供刷新入口，页面不静默覆盖服务端状态。
    expect(screen.getByRole('button', { name: /刷新/ })).toBeInTheDocument()
  })

  it('权限被拒绝时展示 403 错误而不是静默失败', async () => {
    const user = userEvent.setup()
    renderPanel()

    await screen.findAllByText('管理员组')
    await screen.findByText('成员')
    vi.mocked(api.deletePermissionGroup).mockRejectedValue(
      new ApiError('当前账号没有权限组管理权限', { status: 403 }),
    )

    await user.click(screen.getByRole('button', { name: /删除/ }))

    expect(await screen.findByText(/当前账号没有权限组管理权限/)).toBeInTheDocument()
  })

  it('管理者预设只展示强制读取与 Kami 切换两项能力', async () => {
    renderPanel()
    await screen.findByText('成员')
    expect(
      await screen.findByText(/预设只授予 bot\.switch\.kami \+ memory\.read\.force_all/),
    ).toBeInTheDocument()
  })
})
