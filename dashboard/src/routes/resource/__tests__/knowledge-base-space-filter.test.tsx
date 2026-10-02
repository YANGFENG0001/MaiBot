/**
 * W02 知识库空间/分区筛选：所选分区必须真实传到后端 API，而不是只改前端展示。
 *
 * 覆盖两层：
 * 1. 页面层——分区下拉框由真实分区接口驱动，选择后写回 URL / 本地存储并整页跳转；
 * 2. 请求层——使用真实的 `@/lib/memory-api`，验证 `memory_space_id` / `partition_ids`
 *    确实被拼进请求 URL（而不是停留在组件 state 里）。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { KnowledgeBasePage } from '../knowledge-base'
import * as memoryApi from '@/lib/memory-api'
import * as workspacesApi from '@/lib/workspaces-api'

// 用假的 backendApi 承接全部记忆请求，既能拦住网络，又能断言真实请求 URL。
// vi.mock 工厂会被提升到文件顶部，因此这里必须用 vi.hoisted 保证初始化顺序。
const backendApiMock = vi.hoisted(() => ({
  request: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  backendApi: backendApiMock,
  statsApi: backendApiMock,
  authApi: backendApiMock,
  ApiError: class ApiError extends Error {
    readonly status?: number
    constructor(message: string, options: { status?: number } = {}) {
      super(message)
      this.status = options.status
    }
  },
}))

// 记忆 API 模块体量很大且全部走网络，页面层测试按真实导出自动打桩，只保留函数签名。
vi.mock('@/lib/memory-api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/memory-api')>('@/lib/memory-api')
  const stubbed: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(actual)) {
    stubbed[key] = typeof value === 'function' ? vi.fn() : value
  }
  return stubbed
})

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

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

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

const navigateAssign = vi.fn()

const SPACE = {
  id: 'memory-space-a',
  name: '子系统 A 记忆库',
  description: '',
  space_type: 'private' as const,
  enabled: true,
  strict_isolation: false,
  policy_revision: 1,
}

const PARTITION = {
  id: 'memory-partition-shared-a',
  partition_type: 'shared' as const,
  partition_key: 'shared',
  security_domain: 'normal',
  display_name: '共享记忆',
  enabled: true,
  policy_revision: 1,
  object_count: 12,
}

/** 运行时配置必须为真值，页面才会渲染含「记忆空间 / 分区」筛选的运行状态条。 */
const RUNTIME_CONFIG = {
  success: true,
  memory_enabled: true,
  runtime_ready: true,
  embedding_dimension: 1024,
  relation_vectors_enabled: false,
  data_dir: 'data/memory',
  vector_health: { state: 'ok' },
  vector_pools: {},
  available_channels: ['dense'],
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeBasePage />
    </QueryClientProvider>,
  )
}

/** 找到「分区」下拉框的触发器：它是两个 combobox 中不显示记忆空间名的那个。 */
async function findPartitionTrigger() {
  await screen.findByText('分区')
  const triggers = await screen.findAllByRole('combobox')
  const trigger = triggers.find((element) => !element.textContent?.includes(SPACE.name))
  if (!trigger) {
    throw new Error('未找到「分区」下拉框触发器')
  }
  return trigger
}

/** 覆盖 window.location 后，history.replaceState 不再影响它，需要直接写入 search。 */
function setLocationSearch(search: string) {
  ;(window.location as unknown as { search: string }).search = search
}

/** 打开「分区」下拉框并选中测试分区。 */
async function pickPartition(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await findPartitionTrigger())
  await clickOption(user, /12 个对象/)
}

/**
 * 打开下拉框后取最后一个同名选项：Radix 关闭时内容会短暂保留在 DOM 中，
 * 再次打开会同时存在新旧两份，取最后一个即当前弹出的那一份。
 */
async function clickOption(user: ReturnType<typeof userEvent.setup>, text: string | RegExp) {
  const options = await screen.findAllByText(text)
  await user.click(options[options.length - 1])
}

beforeEach(async () => {
  localStorage.setItem('maibot-locale', 'zh')
  await i18n.changeLanguage('zh')
  window.history.replaceState(null, '', '/resource/knowledge-base')
  localStorage.removeItem('memory-console-space-id')
  localStorage.removeItem('memory-console-partition-ids')

  // jsdom 未实现导航，替换掉 assign 以便断言跳转 URL。
  navigateAssign.mockReset()
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: {
      ...window.location,
      assign: navigateAssign,
      pathname: '/resource/knowledge-base',
      search: '',
      hash: '',
    },
  })

  backendApiMock.request.mockReset()
  backendApiMock.request.mockImplementation((_method: string, url: string) => {
    if (String(url).includes('/runtime/config')) {
      return Promise.resolve(RUNTIME_CONFIG)
    }
    return Promise.resolve({ success: true, data: [], config: {}, schema: {}, content: '' })
  })

  vi.mocked(workspacesApi.getWorkspaces).mockResolvedValue({
    success: true,
    data: [],
    memory_spaces: [SPACE],
  })
  vi.mocked(workspacesApi.getMemorySpacePartitions).mockResolvedValue({
    success: true,
    memory_space_id: SPACE.id,
    memory_space_name: SPACE.name,
    strict_isolation: false,
    data: [PARTITION],
  })

  vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValue(RUNTIME_CONFIG as never)
  vi.mocked(memoryApi.getMemoryConfigSchema).mockResolvedValue({
    success: true,
    path: 'config/a_memorix.toml',
    schema: {
      plugin_id: 'a_memorix',
      plugin_info: { name: 'A_Memorix', version: '2.0.0', description: '', author: '' },
      _note: '',
      layout: { type: 'tabs', tabs: [] },
      sections: {},
    },
  } as never)
  vi.mocked(memoryApi.getMemoryConfig).mockResolvedValue({
    success: true,
    path: 'config/a_memorix.toml',
    config: {},
  } as never)
  vi.mocked(memoryApi.getMemoryConfigRaw).mockResolvedValue({ success: true, content: '' } as never)
})

afterEach(() => {
  cleanup()
  localStorage.removeItem('maibot-locale')
  localStorage.removeItem('memory-console-space-id')
  localStorage.removeItem('memory-console-partition-ids')
})

describe('KnowledgeBasePage 分区筛选', () => {
  it('W02 渲染分区下拉框并拉取所选记忆空间的分区列表', async () => {
    renderPage()

    expect(await screen.findByText('分区')).toBeInTheDocument()
    await waitFor(() => {
      expect(workspacesApi.getMemorySpacePartitions).toHaveBeenCalledWith(SPACE.id)
    })
    // 下拉选项来自真实分区接口返回的数据，而不是前端硬编码。
    const user = userEvent.setup()
    await user.click(await findPartitionTrigger())
    expect(await screen.findByText(/12 个对象/)).toBeInTheDocument()
  })

  it('W02 选择分区后写入 URL 与本地存储，跳转参数包含真实分区 ID', async () => {
    const user = userEvent.setup()
    setLocationSearch(`?memory_space_id=${SPACE.id}`)
    renderPage()

    await pickPartition(user)

    expect(localStorage.getItem('memory-console-partition-ids')).toBe(PARTITION.id)
    expect(navigateAssign).toHaveBeenCalled()
    const target = String(navigateAssign.mock.calls[0][0])
    expect(target).toContain(`partition_ids=${encodeURIComponent(PARTITION.id)}`)
    // 切换分区不应丢掉当前记忆空间参数。
    expect(target).toContain(`memory_space_id=${encodeURIComponent(SPACE.id)}`)
  })

  it('W02 切换到全部分区时清除分区参数', async () => {
    const user = userEvent.setup()
    // 模拟「已选中分区并整页跳转后」的状态：参数落在 URL 与本地存储上。
    setLocationSearch(`?memory_space_id=${SPACE.id}&partition_ids=${PARTITION.id}`)
    localStorage.setItem('memory-console-partition-ids', PARTITION.id)
    renderPage()

    // 下拉框回显当前分区，而不是停留在「全部分区」。
    expect(await findPartitionTrigger()).toHaveTextContent('12 个对象')

    await user.click(await findPartitionTrigger())
    await clickOption(user, '全部分区（按权限范围）')

    expect(localStorage.getItem('memory-console-partition-ids')).toBeNull()
    const target = String(navigateAssign.mock.calls.at(-1)?.[0] ?? '')
    expect(target).not.toContain('partition_ids=')
    expect(target).toContain(`memory_space_id=${encodeURIComponent(SPACE.id)}`)
  })
})

describe('记忆请求真实携带作用域参数', () => {
  it('W02 已选空间与分区时，请求 URL 同时携带 memory_space_id 与 partition_ids', async () => {
    localStorage.setItem('memory-console-space-id', SPACE.id)
    localStorage.setItem('memory-console-partition-ids', PARTITION.id)

    // 使用真实实现，验证请求层确实把分区参数拼进 URL。
    const real = await vi.importActual<typeof import('@/lib/memory-api')>('@/lib/memory-api')
    await real.getMemoryConfig()

    const [, url] = backendApiMock.request.mock.calls[0]
    expect(String(url)).toContain(`memory_space_id=${encodeURIComponent(SPACE.id)}`)
    expect(String(url)).toContain(`partition_ids=${encodeURIComponent(PARTITION.id)}`)
  })

  it('W02 未选择分区时只发送 memory_space_id', async () => {
    localStorage.setItem('memory-console-space-id', SPACE.id)

    const real = await vi.importActual<typeof import('@/lib/memory-api')>('@/lib/memory-api')
    await real.getMemoryConfig()

    const [, url] = backendApiMock.request.mock.calls[0]
    expect(String(url)).toContain(`memory_space_id=${encodeURIComponent(SPACE.id)}`)
    expect(String(url)).not.toContain('partition_ids=')
  })

  it('W02 URL 中的分区参数优先于本地存储', async () => {
    setLocationSearch(`?memory_space_id=${SPACE.id}&partition_ids=${PARTITION.id}`)
    localStorage.setItem('memory-console-space-id', SPACE.id)
    localStorage.setItem('memory-console-partition-ids', 'memory-partition-stale')

    const real = await vi.importActual<typeof import('@/lib/memory-api')>('@/lib/memory-api')
    await real.getMemoryConfig()

    const [, url] = backendApiMock.request.mock.calls[0]
    expect(String(url)).toContain(`partition_ids=${encodeURIComponent(PARTITION.id)}`)
    expect(String(url)).not.toContain('memory-partition-stale')
  })
})
