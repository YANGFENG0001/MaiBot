import type { ReactNode } from 'react'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AboutTab } from '../AboutTab'
import { SettingsPage } from '../index'

const scrollAreaState = vi.hoisted(() => ({ attachViewportRef: true }))
const navigateMock = vi.hoisted(() => vi.fn())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@tanstack/react-router', () => ({
  // 上游 1.3.5 把设置页从「内嵌进麦麦设置（/config/bot?mode=webui）」改回独立路由 /settings，
  // 旧链接的重定向下沉到 /config/bot 的 beforeLoad；组件本身仍从 router 读取 searchStr 与 hash。
  // 这里直接以 jsdom 的 window.location 作为来源，使 window.history.replaceState 构造的 URL 依然生效。
  useNavigate: () => navigateMock,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { searchStr: string; hash: string } }) => unknown
  }) => select({ location: { searchStr: window.location.search, hash: window.location.hash } }),
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({
    children,
    viewportRef,
  }: {
    children: ReactNode
    viewportRef?: { current: HTMLDivElement | null }
  }) => (
    <div
      data-testid="settings-scroll-viewport"
      ref={(node) => {
        if (scrollAreaState.attachViewportRef && viewportRef) {
          viewportRef.current = node
        }
      }}
    >
      {children}
    </div>
  ),
}))

vi.mock('@/components/ui/tabs', async () => {
  const { createContext, useContext } = await import('react')

  type TabsContextValue = {
    value: string
    onValueChange: (value: string) => void
  }

  const TabsContext = createContext<TabsContextValue | null>(null)

  return {
    Tabs: ({
      children,
      value,
      onValueChange,
    }: {
      children: ReactNode
      value: string
      onValueChange: (value: string) => void
    }) => (
      <TabsContext.Provider value={{ value, onValueChange }}>
        <div data-testid="settings-tabs" data-value={value}>
          {children}
          <button
            type="button"
            data-testid="settings-invalid-tab"
            onClick={() => onValueChange('not-a-tab')}
          />
        </div>
      </TabsContext.Provider>
    ),
    TabsList: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    TabsTrigger: ({ children, value }: { children: ReactNode; value: string }) => {
      const context = useContext(TabsContext)
      return (
        <button type="button" onClick={() => context?.onValueChange(value)}>
          {children}
        </button>
      )
    },
    TabsContent: ({ children, value }: { children: ReactNode; value: string }) => {
      const context = useContext(TabsContext)
      return context?.value === value ? <section>{children}</section> : null
    },
  }
})

vi.mock('../AppearanceTab', () => ({
  AppearanceTab: () => <div>外观页内容</div>,
}))

vi.mock('../SecurityTab', () => ({
  SecurityTab: () => <div>安全页内容</div>,
}))

vi.mock('../OtherTab', () => ({
  OtherTab: () => <div>其他页内容</div>,
}))

describe('设置页入口与关于页', () => {
  const scrollTo = vi.fn()

  beforeEach(() => {
    scrollAreaState.attachViewportRef = true
    window.history.replaceState(null, '', '/settings')
    Object.defineProperty(HTMLDivElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        callback(0)
        return 1
      })
    )
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  afterEach(() => {
    cleanup()
    scrollTo.mockReset()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('优先读取查询参数，并通过路由跳转同步标签', async () => {
    window.history.replaceState(null, '', '/settings?tab=security')
    const user = userEvent.setup()

    render(<SettingsPage />)

    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'security')
    expect(screen.getByText('安全页内容')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'settings.tabs.about' }))

    // 上游 1.3.5 起设置页回到独立路由：切换标签不再自行改写 window.history 与滚动位置，
    // 而是经 router 跳转到 /settings?tab=...（滚动由外层页面统一管理）。
    expect(navigateMock).toHaveBeenLastCalledWith({
      href: '/settings?tab=about',
      replace: true,
    })
  })

  it('切换标签时保留其他查询参数，并剔除遗留的 mode=webui', async () => {
    window.history.replaceState(null, '', '/settings?mode=webui&tab=security&extra=keep')
    const user = userEvent.setup()

    render(<SettingsPage />)
    expect(screen.getByText('安全页内容')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'settings.tabs.other' }))

    // 上游把 mode=webui 视为旧内嵌链接的残留标记，跳转时会一并删除。
    expect(navigateMock).toHaveBeenLastCalledWith({
      href: '/settings?tab=other&extra=keep',
      replace: true,
    })
  })

  it('切回外观页时删除 tab 参数，也不保留 mode', async () => {
    window.history.replaceState(null, '', '/settings?tab=about&mode=webui')
    const user = userEvent.setup()

    render(<SettingsPage />)
    await user.click(screen.getByRole('button', { name: 'settings.tabs.appearance' }))

    expect(navigateMock).toHaveBeenLastCalledWith({ href: '/settings', replace: true })
  })

  it('查询参数中的无效标签回退到外观页', () => {
    window.history.replaceState(null, '', '/settings?tab=unknown')
    render(<SettingsPage />)

    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'appearance')
    expect(screen.getByText('外观页内容')).toBeInTheDocument()
  })

  it('hash 页签在组件读取侧仍然兼容旧书签', () => {
    window.history.replaceState(null, '', '/settings#other')
    render(<SettingsPage />)

    // 组件仍会读 state.location.hash，作为 /config/bot?mode=webui 旧链接重定向后的兜底。
    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'other')
    expect(screen.getByText('其他页内容')).toBeInTheDocument()
  })

  it('未知标签值在读取侧回退外观页，写入侧不校验', async () => {
    window.history.replaceState(null, '', '/settings?tab=about')
    const user = userEvent.setup()

    render(<SettingsPage />)
    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'about')

    await user.click(screen.getByTestId('settings-invalid-tab'))

    // handleTabChange 不校验取值（校验只发生在派生 activeTab 时），未知值会原样写进 URL；
    // 待路由更新后 searchStr 变为 tab=not-a-tab，读取侧才会回退到 appearance。
    expect(navigateMock).toHaveBeenLastCalledWith({
      href: '/settings?tab=not-a-tab',
      replace: true,
    })
    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'about')
  })

  it('关于页展示版本、技术栈、许可证和安全的外部链接属性', () => {
    render(<AboutTab />)

    expect(screen.getByText(/MaiBot Dashboard/)).toBeInTheDocument()
    expect(screen.getByText('React 19.2.0')).toBeInTheDocument()
    expect(screen.getByText('GPLv3')).toBeInTheDocument()
    expect(screen.getByText('TypeScript')).toBeInTheDocument()

    expect(screen.getByRole('link', { name: /settings.about.visitGitHub/ })).toHaveAttribute(
      'href',
      'https://github.com/Mai-with-u/MaiBot-Dashboard'
    )
    expect(screen.getByRole('link', { name: '@MotricSeven' })).toHaveAttribute(
      'rel',
      'noopener noreferrer'
    )
  })

  it('设置页不再自带滚动容器与可折叠标题，滚动交由外层页面管理', () => {
    render(<SettingsPage />)

    expect(screen.queryByTestId('settings-scroll-viewport')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'settings.title' })).not.toBeInTheDocument()
    expect(screen.getByTestId('settings-tabs')).toBeInTheDocument()
  })
})
