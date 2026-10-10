import type { ReactNode } from 'react'

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  Toast,
  ToastAction,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from '../toast'

const { mobileState } = vi.hoisted(() => ({
  mobileState: { value: false },
}))

vi.mock('@/hooks/use-media-query', () => ({
  useIsMobile: () => mobileState.value,
}))

/** Toast 必须挂在 Provider + Viewport 下，Radix 才会把内容传送进视口 */
function renderToast(toast: ReactNode, viewportClassName?: string) {
  return render(
    <ToastProvider>
      {toast}
      <ToastViewport className={viewportClassName} />
    </ToastProvider>
  )
}

function queryToast(suffix = '') {
  return document.querySelector(`[data-dashboard-toast${suffix}="true"]`)
}

/**
 * 合并进来的上游 1.3.5 把进度条从 CSS 动画（inline `animationDuration` /
 * `animationPlayState`）换成了 Web Animations API（`element.animate()` +
 * `updatePlaybackRate()`）：播放速率不再能从 DOM 上读到，只能在 animate 返回的
 * Animation 对象上观察，所以这里补一个最小实现并记录调用。
 *
 * 两个必须注意的点：
 * 1. jsdom 根本没有实现 Web Animations API，`Element.prototype.animate` 是
 *    undefined，因此不能用 `vi.spyOn`（会抛 "The property animate is not defined"），
 *    只能用 `Object.defineProperty` 新装一个。
 * 2. 这个桩**只能**留在本文件里，绝不能提到全局 setup.ts：framer-motion 一旦探测到
 *    `Element.prototype.animate` 存在，就会改用 WAAPI 驱动自己的 duration 类动画并
 *    等待 `animation.finished`，而 jsdom 下的假动画永远不会 finish，会让
 *    `AnimatePresence mode="wait"` 的退场永久挂起（model.test.tsx 等页面测试会大面积失败）。
 *
 * 另外用普通函数而不是 `vi.fn()`：vitest 配置了 mockReset，`vi.fn()` 会在每个用例前
 * 被重置成空实现。每次调用返回的假动画对象是在函数体内新建的 `vi.fn()`，不受影响。
 */
const animateCalls: Array<{ keyframes: unknown; options: unknown }> = []
const animations: Array<{
  cancel: ReturnType<typeof vi.fn>
  updatePlaybackRate: ReturnType<typeof vi.fn>
}> = []

Object.defineProperty(Element.prototype, 'animate', {
  configurable: true,
  writable: true,
  value: (keyframes: unknown, options: unknown) => {
    animateCalls.push({ keyframes, options })
    const animation = { cancel: vi.fn(), updatePlaybackRate: vi.fn() }
    animations.push(animation)
    return animation as unknown as Animation
  },
})

/** 取最近一次 animate 调用，避免断言被同一次渲染中的重复挂载干扰 */
function lastAnimateCall() {
  return animateCalls[animateCalls.length - 1]
}

beforeEach(() => {
  mobileState.value = false
  animateCalls.length = 0
  animations.length = 0
})

describe('ToastViewport', () => {
  it('桌面端靠右堆叠，并带视口标记与自定义 class', () => {
    render(
      <ToastProvider>
        <ToastViewport className="extra-viewport" />
      </ToastProvider>
    )

    const viewport = screen.getByRole('list')
    expect(viewport).toHaveAttribute('data-dashboard-toast-viewport', 'true')
    expect(viewport).toHaveClass('top-0', 'right-0', 'sm:max-w-[420px]', 'extra-viewport')
    expect(viewport).not.toHaveClass('items-center')
  })

  it('移动端顶部居中排列', () => {
    mobileState.value = true
    render(
      <ToastProvider>
        <ToastViewport />
      </ToastProvider>
    )

    const viewport = screen.getByRole('list')
    expect(viewport).toHaveClass('top-0', 'left-0', 'right-0', 'items-center')
    expect(viewport.className).not.toContain('sm:max-w-[420px]')
  })
})

describe('Toast', () => {
  it('桌面端使用 default 变体与从右侧滑入的位置类', () => {
    renderToast(
      <Toast open className="extra-toast">
        <ToastTitle>普通标题</ToastTitle>
      </Toast>
    )

    const toast = queryToast()
    expect(toast).not.toBeNull()
    expect(toast).toHaveClass(
      'bg-primary/5',
      'data-[state=open]:animate-slide-in-from-right',
      'extra-toast'
    )
    expect(toast).not.toHaveClass('data-[state=open]:animate-slide-in-from-top')
  })

  it('移动端 destructive 变体改用从顶部滑入', () => {
    mobileState.value = true
    renderToast(
      <Toast open variant="destructive">
        <ToastTitle>危险</ToastTitle>
      </Toast>
    )

    const toast = queryToast()
    expect(toast).toHaveClass(
      'destructive',
      'border-destructive',
      'data-[state=open]:animate-slide-in-from-top'
    )
    expect(toast).not.toHaveClass('data-[state=open]:animate-slide-in-from-right')
  })

  it('仅在有限且大于 0 的 duration 下渲染进度条', () => {
    const { rerender } = renderToast(
      <Toast open duration={2400}>
        <ToastTitle>有进度</ToastTitle>
      </Toast>
    )

    const progress = queryToast('-progress')
    expect(progress).not.toBeNull()
    // 时长交给 element.animate()，不再写进 inline style。
    expect(lastAnimateCall()).toEqual({
      keyframes: [{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }],
      options: { duration: 2400, easing: 'linear', fill: 'forwards' },
    })

    rerender(
      <ToastProvider>
        <Toast open duration={0}>
          <ToastTitle>零时长</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()

    rerender(
      <ToastProvider>
        <Toast open duration={Number.POSITIVE_INFINITY}>
          <ToastTitle>无限</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()

    rerender(
      <ToastProvider>
        <Toast open duration={Number.NaN}>
          <ToastTitle>非数字</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()

    rerender(
      <ToastProvider>
        <Toast open>
          <ToastTitle>未传 duration</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    // 上游 1.3.5 给 duration 加了 4000ms 默认值，未传时同样渲染进度条。
    expect(queryToast('-progress')).not.toBeNull()
    expect(lastAnimateCall()).toEqual({
      keyframes: [{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }],
      options: { duration: 4000, easing: 'linear', fill: 'forwards' },
    })
  })

  it('视口暂停 / 恢复时同步进度动画，并转发 onPause / onResume', () => {
    const onPause = vi.fn()
    const onResume = vi.fn()

    renderToast(
      <Toast open duration={8000} onPause={onPause} onResume={onResume}>
        <ToastTitle>可暂停</ToastTitle>
      </Toast>
    )

    const region = screen.getByRole('region', { name: /Notifications/i })
    expect(queryToast('-progress')).not.toBeNull()
    const animation = animations[0]
    expect(animation.updatePlaybackRate).toHaveBeenLastCalledWith(1)

    fireEvent.pointerMove(region)
    expect(onPause).toHaveBeenCalledTimes(1)
    // 悬停时减速到 1/3，而不是整体暂停。
    expect(animation.updatePlaybackRate).toHaveBeenLastCalledWith(1 / 3)

    fireEvent.pointerLeave(region)
    expect(onResume).toHaveBeenCalledTimes(1)
    expect(animation.updatePlaybackRate).toHaveBeenLastCalledWith(1)
  })

  it('未传入 onPause / onResume 时仍能切换进度条播放状态', () => {
    renderToast(
      <Toast open duration={8000}>
        <ToastTitle>无回调</ToastTitle>
      </Toast>
    )

    const region = screen.getByRole('region', { name: /Notifications/i })
    const animation = animations[0]

    fireEvent.pointerMove(region)
    expect(animation.updatePlaybackRate).toHaveBeenLastCalledWith(1 / 3)

    fireEvent.pointerLeave(region)
    expect(animation.updatePlaybackRate).toHaveBeenLastCalledWith(1)
  })
})

describe('Toast 子组件', () => {
  it('Title / Description / Action / Close 带标记、文案与无障碍属性', () => {
    renderToast(
      <Toast open>
        <ToastTitle className="extra-title">标题文本</ToastTitle>
        <ToastDescription className="extra-desc">描述文本</ToastDescription>
        <ToastAction altText="撤销刚才的操作" className="extra-action">
          撤销
        </ToastAction>
        <ToastClose className="extra-close" />
      </Toast>
    )

    const title = queryToast('-title')
    const description = queryToast('-description')
    const action = queryToast('-action')
    const close = queryToast('-close')

    expect(title).toHaveTextContent('标题文本')
    expect(title).toHaveClass('font-semibold', 'extra-title')
    expect(description).toHaveTextContent('描述文本')
    expect(description).toHaveClass('select-text', 'extra-desc')
    expect(action).toHaveTextContent('撤销')
    expect(action).toHaveClass('extra-action')
    expect(close).toHaveAttribute('aria-label', '关闭提示')
    expect(close).toHaveAttribute('toast-close', '')
    expect(close).toHaveClass('extra-close')
  })
})
