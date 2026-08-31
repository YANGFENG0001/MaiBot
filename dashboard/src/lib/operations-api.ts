import { backendApi } from '@/lib/http'

export interface OperationMirror {
  id: string
  name: string
  enabled: boolean
  priority: number
}

export interface NapCatOperationStatus {
  id: string
  name: string
  state: 'ready' | 'login_required' | 'unreachable'
  websocket_ready: boolean
  webui_ready: boolean
  diagnosis: string
  runtime_mounted: boolean
  runtime_root: string
  account: string
  webui_token: string
  onebot_token: string
  onebot_token_consistent: boolean
  onebot_config_count: number
  sync_supported: boolean
}

export interface OperationsOverview {
  success: boolean
  services: {
    maibot: { state: string; name: string }
    napcat: NapCatOperationStatus
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
