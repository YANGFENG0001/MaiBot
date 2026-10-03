/**
 * Kami 管理面板：危险提示、会话列表、强制撤销与 profile 开关的乐观锁。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { KamiPanel } from '@/components/workspaces/kami-panel'
import { ApiError } from '@/lib/http'
import * as api from '@/lib/memory-audit-api'

vi.mock('@/lib/memory-audit-api', () => ({
  getMemoryAccessAudit: vi.fn(),
  getBotControlAudit: vi.fn(),
  getKamiProfile: vi.fn(),
  updateKamiProfile: vi.fn(),
  listKamiSessions: vi.fn(),
  revokeKamiSession: vi.fn(),
}))

const PROFILE = {
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
  policy_revision: 7,
}

const SESSION = {
  id: 'state-1',
  session_id: 'session-1',
  person_id: 'person-1',
  kami_bot_profile_id: 'bot-profile-kami',
  activated_from_bot_profile_id: 'bot-profile-public',
  permission_group_id: 'group-1',
  status: 'active',
  activated_at: '2026-10-03T00:00:00',
  expires_at: '2026-10-03T01:00:00',
  last_used_at: '2026-10-03T00:10:00',
  revision: 1,
  expired: false,
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <KamiPanel />
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  localStorage.setItem('maibot-locale', 'zh')
  await i18n.changeLanguage('zh')
  vi.mocked(api.getKamiProfile).mockResolvedValue(PROFILE)
  vi.mocked(api.listKamiSessions).mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  localStorage.removeItem('maibot-locale')
})

describe('KamiPanel', () => {
  it('始终展示 Kami 高风险提示与安全域信息', async () => {
    renderPanel()
    expect(await screen.findByText('危险操作区域')).toBeInTheDocument()
    expect(await screen.findByText('memory-space-kami')).toBeInTheDocument()
    expect(await screen.findByText('900s / max 86400s')).toBeInTheDocument()
  })

  it('没有活动会话时展示空状态', async () => {
    renderPanel()
    expect(await screen.findByText(/当前没有 Kami 会话记录/)).toBeInTheDocument()
  })

  it('展示活动会话并在撤销时调用后端', async () => {
    const user = userEvent.setup()
    vi.mocked(api.listKamiSessions).mockResolvedValue([SESSION])
    vi.mocked(api.revokeKamiSession).mockResolvedValue(true)
    renderPanel()

    expect(await screen.findByText('person-1 · session-1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /强制撤销/ }))

    await waitFor(() => {
      expect(api.revokeKamiSession).toHaveBeenCalledWith('state-1')
    })
  })

  it('已撤销的会话不能再被撤销', async () => {
    vi.mocked(api.listKamiSessions).mockResolvedValue([{ ...SESSION, status: 'revoked' }])
    renderPanel()

    await screen.findByText('person-1 · session-1')
    expect(screen.getByRole('button', { name: /强制撤销/ })).toBeDisabled()
  })

  it('切换启用开关时携带 expected_revision 做乐观锁', async () => {
    const user = userEvent.setup()
    vi.mocked(api.updateKamiProfile).mockResolvedValue({ ...PROFILE, enabled: false })
    renderPanel()

    await screen.findByText('memory-space-kami')
    await user.click(screen.getAllByRole('switch')[0])

    await waitFor(() => {
      expect(api.updateKamiProfile).toHaveBeenCalledWith({ enabled: false, expected_revision: 7 })
    })
  })

  it('409 冲突时把错误暴露在页面上', async () => {
    const user = userEvent.setup()
    vi.mocked(api.updateKamiProfile).mockRejectedValue(
      new ApiError('配置已被其他管理员更新，请刷新后重试', { status: 409 }),
    )
    renderPanel()

    await screen.findByText('memory-space-kami')
    await user.click(screen.getAllByRole('switch')[0])

    expect(await screen.findByText(/配置已被其他管理员更新，请刷新后重试/)).toBeInTheDocument()
  })
})
