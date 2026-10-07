import { Link } from '@tanstack/react-router'
import { Activity, DatabaseBackup, ExternalLink, PlugZap, QrCode, RefreshCw, Server, Settings2, Wifi } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useToast } from '@/hooks/use-toast'
import { getOperationsOverview, syncAdapterRuntime } from '@/lib/operations-api'
import type { OperationsOverview } from '@/lib/operations-api'

function statusVariant(state: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (state === 'ready') return 'default'
  if (state === 'login_required') return 'secondary'
  return 'destructive'
}

export function OperationsPage() {
  const { toast } = useToast()
  const [overview, setOverview] = useState<OperationsOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)

  const snowLumaWebUiPort = overview?.services.snowluma?.webui_port ?? 6099
  const snowLuma = overview?.services.snowluma

  const snowLumaWebUiUrl = useMemo(() => {
    if (typeof window === 'undefined') return ''
    return `${window.location.protocol}//${window.location.hostname}:${snowLumaWebUiPort}/`
  }, [snowLumaWebUiPort])

  // SnowLuma 官方控制台只做配置管理，没有 QQ 扫码界面；扫码页由部署侧
  // （nginx 反代的 Xvfb 截图服务）提供，因此这里按独立的端口拼装地址。
  const snowLumaQrUrl = useMemo(() => {
    const rawPath = snowLuma?.qr_url || '/qq-qr'
    if (typeof window === 'undefined') return rawPath
    if (/^https?:\/\//i.test(rawPath)) return rawPath
    const qrPort = snowLuma?.qr_port ?? 80
    const portSuffix = qrPort && qrPort !== 80 && qrPort !== 443 ? `:${qrPort}` : ''
    return `${window.location.protocol}//${window.location.hostname}${portSuffix}${rawPath}`
  }, [snowLuma?.qr_port, snowLuma?.qr_url])

  const tokenSourceLabel = useMemo(() => {
    if (snowLuma?.token_source === 'environment') return '环境变量锁定'
    if (snowLuma?.token_source === 'adapter_plugin') return '适配器配置（自动同步）'
    return '未配置'
  }, [snowLuma?.token_source])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setOverview(await getOperationsOverview())
    } catch (error) {
      toast({
        title: '运行中心加载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleSync = useCallback(async () => {
    setSyncing(true)
    try {
      const result = await syncAdapterRuntime('maibot-team.snowluma-adapter')
      toast({ title: 'SnowLuma 配置同步完成', description: result.sync.message })
      await refresh()
    } catch (error) {
      toast({
        title: 'SnowLuma 配置同步失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setSyncing(false)
    }
  }, [refresh, toast])

  const enabledMirrors = overview?.mirrors.filter((mirror) => mirror.enabled) ?? []

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">运行中心</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            服务器版 OneKey 控制台：服务状态、适配器、日志、镜像源、更新与数据迁移。
          </p>
        </div>
        <Button variant="outline" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          刷新状态
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2 text-lg"><Server className="h-5 w-5" />MaiBot Core</CardTitle>
              <Badge>运行中</Badge>
            </div>
            <CardDescription>当前 WebUI 与核心进程处于同一服务。</CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Button asChild size="sm" variant="outline"><Link to="/logs">查看日志</Link></Button>
            <Button asChild size="sm" variant="outline"><Link to="/config/bot">主程序配置</Link></Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2 text-lg"><Wifi className="h-5 w-5" />SnowLuma 适配器</CardTitle>
              <Badge variant={statusVariant(snowLuma?.state ?? 'unreachable')}>
                {snowLuma?.state === 'ready' ? '已连接' : snowLuma?.state === 'login_required' ? '等待登录' : '不可达'}
              </Badge>
            </div>
            <CardDescription>{snowLuma?.diagnosis ?? '正在读取状态…'}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-2 text-muted-foreground">
              <span>当前 QQ</span><span className="text-foreground">{snowLuma?.account || '未选择'}</span>
              <span>OneBot Token</span><span className="font-mono text-foreground">{snowLuma?.onebot_token ?? '未读取'}</span>
              <span>令牌来源</span><span className="text-foreground">{tokenSourceLabel}</span>
              <span>控制台端口</span><span className="font-mono text-foreground">{snowLumaWebUiPort}</span>
            </div>
            {snowLuma && !snowLuma.onebot_token_consistent && (
              <p className="rounded-md bg-destructive/10 p-2 text-destructive">
                多个 OneBot 配置的 Token 不一致，点击「同步 Token」立即修复。
              </p>
            )}
            {snowLuma?.restart_required && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-amber-700 dark:text-amber-300">
                检测到令牌漂移，已按 MaiBot 侧配置自动改回；需重启 SnowLuma 协议端后生效。
              </p>
            )}
            {snowLuma?.token_managed && (
              <p className="text-xs text-muted-foreground">
                该令牌由 MaiBot 托管，协议端无法自行变更。
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm">
                <a href={snowLumaQrUrl} target="_blank" rel="noreferrer">
                  <QrCode className="mr-2 h-4 w-4" />QQ 扫码登录
                </a>
              </Button>
              <Button asChild size="sm" variant="outline"><a href={snowLumaWebUiUrl} target="_blank" rel="noreferrer">SnowLuma 控制台<ExternalLink className="ml-2 h-4 w-4" /></a></Button>
              <Button asChild size="sm" variant="outline"><Link to="/adapter-management">适配器设置</Link></Button>
              <Button size="sm" variant="outline" onClick={() => void handleSync()} disabled={syncing || !snowLuma?.sync_supported}>
                <RefreshCw className={`mr-2 h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />同步 Token
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg"><PlugZap className="h-5 w-5" />镜像源</CardTitle>
            <CardDescription>当前启用 {enabledMirrors.length} 个源，按优先级自动切换。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {enabledMirrors.slice(0, 3).map((mirror) => (
              <div key={mirror.id} className="flex items-center justify-between rounded-md border px-3 py-2">
                <span>{mirror.name}</span><Badge variant="outline">#{mirror.priority}</Badge>
              </div>
            ))}
            <Button asChild size="sm" variant="outline"><Link to="/plugin-mirrors">管理镜像源</Link></Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">OneKey 功能入口</CardTitle>
          <CardDescription>桌面端常用管理功能已在服务器 WebUI 中统一入口。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Button asChild variant="outline" className="h-auto justify-start py-4"><Link to="/adapter-management"><Wifi className="mr-3 h-5 w-5" /><span className="text-left">适配器管理<br /><span className="text-xs text-muted-foreground">配置与作用域</span></span></Link></Button>
          <Button asChild variant="outline" className="h-auto justify-start py-4"><Link to="/logs"><Activity className="mr-3 h-5 w-5" /><span className="text-left">实时日志<br /><span className="text-xs text-muted-foreground">终端与故障定位</span></span></Link></Button>
          <Button asChild variant="outline" className="h-auto justify-start py-4"><Link to="/plugin-mirrors"><Settings2 className="mr-3 h-5 w-5" /><span className="text-left">更新与镜像源<br /><span className="text-xs text-muted-foreground">插件下载线路</span></span></Link></Button>
          <Button asChild variant="outline" className="h-auto justify-start py-4"><Link to="/data-transfer"><DatabaseBackup className="mr-3 h-5 w-5" /><span className="text-left">数据迁移<br /><span className="text-xs text-muted-foreground">导入、导出与备份</span></span></Link></Button>
        </CardContent>
      </Card>

      {overview?.security && (
        <p className="text-xs text-muted-foreground">{overview.security.message}</p>
      )}
    </div>
  )
}

export default OperationsPage
