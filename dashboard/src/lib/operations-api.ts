import { backendApi } from '@/lib/http'

export interface OperationMirror {
  id: string
  name: string
  enabled: boolean
  priority: number
}

export interface SnowLumaOperationStatus {
  id: string
  name: string
  state: 'ready' | 'login_required' | 'unreachable'
  websocket_ready: boolean
  webui_ready: boolean
  webui_port: number
  /** QQ 扫码登录页路径（SnowLuma 控制台本身没有扫码界面，由部署侧提供） */
  qr_url: string
  /** 扫码页对外端口，默认 80 */
  qr_port: number
  diagnosis: string
  runtime_mounted: boolean
  runtime_root: string
  account: string
  onebot_token: string
  onebot_token_consistent: boolean
  onebot_config_count: number
  sync_supported: boolean
  /** 权威令牌来源：environment（环境变量硬锁定）/ adapter_plugin（适配器配置）/ unset */
  token_source: 'environment' | 'adapter_plugin' | 'unset' | string
  token_managed: boolean
  /** 本轮巡检是否改写了运行时配置（改写后需重启协议端才生效） */
  restart_required: boolean
  changed_paths: string[]
}

export interface OperationsOverview {
  success: boolean
  services: {
    maibot: { state: string; name: string }
    snowluma: SnowLumaOperationStatus
  }
  mirrors: OperationMirror[]
  security: { container_control_available: boolean; message: string }
}

export async function getOperationsOverview(): Promise<OperationsOverview> {
  return backendApi.get<OperationsOverview>('/api/webui/operations/overview', {
    errorMessage: '获取运行中心状态失败',
  })
}

export async function syncAdapterRuntime(pluginId: string): Promise<{
  success: boolean
  sync: { message: string; changed_paths: string[] }
}> {
  return backendApi.post(`/api/webui/plugins/config/${pluginId}/sync-adapter-runtime`, {
    errorMessage: '同步适配器运行时配置失败',
  })
}
