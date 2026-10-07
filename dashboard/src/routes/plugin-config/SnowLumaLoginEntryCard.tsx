import { useQuery } from '@tanstack/react-query'
import { ExternalLink, QrCode } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { getOperationsOverview, type SnowLumaOperationStatus } from '@/lib/operations-api'

const SNOWLUMA_QR_FALLBACK_PATH = '/qq-qr'
const SNOWLUMA_CONSOLE_FALLBACK_PORT = 6099

/** 把后端给出的路径拼成浏览器可直接打开的地址；已是绝对地址时原样返回。 */
function resolvePublicUrl(path: string, port: number): string {
  if (typeof window === 'undefined') return path
  if (/^https?:\/\//i.test(path)) return path
  const portSuffix = port && port !== 80 && port !== 443 ? `:${port}` : ''
  return `${window.location.protocol}//${window.location.hostname}${portSuffix}${path}`
}

/**
 * SnowLuma 协议端登录入口。
 *
 * SnowLuma 官方控制台只做配置管理，其前端整包内没有任何 QQ 扫码界面；扫码页由部署侧的
 * nginx 反代提供。这里给出直达入口，避免用户点进控制台后找不到登录位置。
 */
export function SnowLumaLoginEntryCard() {
  const overviewQuery = useQuery({
    queryKey: ['operations-overview'],
    queryFn: getOperationsOverview,
    retry: false,
  })

  const snowluma: SnowLumaOperationStatus | undefined = overviewQuery.data?.services.snowluma
  const qrUrl = resolvePublicUrl(snowluma?.qr_url || SNOWLUMA_QR_FALLBACK_PATH, snowluma?.qr_port ?? 80)
  const consoleUrl = resolvePublicUrl('/', snowluma?.webui_port ?? SNOWLUMA_CONSOLE_FALLBACK_PORT)

  return (
    <section className="py-2" aria-label="SnowLuma 协议端登录">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
        <div className="space-y-1">
          <p className="flex items-center gap-2 text-sm font-medium">
            <QrCode className="h-4 w-4" />
            SnowLuma 协议端登录
          </p>
          <p className="text-muted-foreground text-xs">
            SnowLuma 控制台只提供配置管理，没有 QQ 扫码界面；扫码请使用右侧的「QQ 扫码登录」入口。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm">
            <a href={qrUrl} target="_blank" rel="noreferrer">
              <QrCode className="mr-2 h-4 w-4" />
              QQ 扫码登录
            </a>
          </Button>
          <Button asChild size="sm" variant="outline">
            <a href={consoleUrl} target="_blank" rel="noreferrer">
              SnowLuma 控制台
              <ExternalLink className="ml-2 h-4 w-4" />
            </a>
          </Button>
        </div>
      </div>
    </section>
  )
}
