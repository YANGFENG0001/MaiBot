import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/memory-transfers'

/** 每次请求都要携带的会话/人员作用域，后端据此解析真实权限。 */
export interface TransferScope {
  session_id: string
  person_id: string
  audience_type?: 'private' | 'group'
}

export interface TransferJob {
  id: string
  status: string
  mode: 'link' | 'copy' | 'promote' | string
  source_space_id: string
  target_space_id: string
  approval_required: boolean
  approval_state: string
  plan_hash: string
  policy_snapshot_hash: string
  plan_revision: number
  source_space_revision: number
  target_space_revision: number
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  last_error_code: string
  created_at: string
  updated_at: string
}

export interface TransferItem {
  id: number
  job_id: string
  mode: string
  object_type: string
  source_object_id: string
  target_object_id: string
  source_space_id: string
  source_partition_id: string
  target_space_id: string
  target_partition_id: string
  status: string
  conflict_code: string
  error_code: string
  attempt_count: number
  created_at: string
  updated_at: string
}

export interface TransferCreateInput extends TransferScope {
  mode: 'link' | 'copy' | 'promote'
  source_space_id: string
  source_partition_ids: string[]
  target_space_id: string
  target_partition_id: string
  object_types: string[]
  object_ids?: string[]
  approval_policy?: 'manual' | 'auto_safe'
  conflict_policy?: 'skip' | 'fail'
  idempotency_key?: string
}

export interface ApprovalInput extends TransferScope {
  plan_hash: string
  plan_revision: number
  policy_revision: number
  policy_snapshot_hash: string
  comment?: string
}

export async function listTransfers(
  scope: TransferScope,
  options: { limit?: number; offset?: number } = {},
): Promise<TransferJob[]> {
  const response = await backendApi.get<{ data: TransferJob[] }>(API_BASE, {
    query: {
      session_id: scope.session_id,
      person_id: scope.person_id,
      audience_type: scope.audience_type ?? 'private',
      limit: options.limit ?? 100,
      offset: options.offset ?? 0,
    },
    cache: 'no-store',
    errorMessage: '读取记忆转移任务失败',
  })
  return response.data
}

export async function getTransfer(
  jobId: string,
  scope: TransferScope,
  includeItems = false,
): Promise<{ job: TransferJob; items: TransferItem[] }> {
  const response = await backendApi.get<{ data: TransferJob; items?: TransferItem[] }>(
    `${API_BASE}/${encodeURIComponent(jobId)}`,
    {
      query: {
        session_id: scope.session_id,
        person_id: scope.person_id,
        audience_type: scope.audience_type ?? 'private',
        include_items: includeItems,
      },
      cache: 'no-store',
      errorMessage: '读取记忆转移任务详情失败',
    },
  )
  return { job: response.data, items: response.items ?? [] }
}

export async function createTransfer(input: TransferCreateInput): Promise<TransferJob> {
  return backendApi.post<TransferJob>(API_BASE, {
    body: {
      ...input,
      object_ids: input.object_ids ?? [],
      approval_policy: input.approval_policy ?? 'manual',
      conflict_policy: input.conflict_policy ?? 'skip',
      idempotency_key: input.idempotency_key ?? '',
    },
    errorMessage: '创建记忆转移任务失败',
  })
}

function scopeBody(scope: TransferScope) {
  return {
    session_id: scope.session_id,
    person_id: scope.person_id,
    audience_type: scope.audience_type ?? 'private',
  }
}

export async function planTransfer(jobId: string, scope: TransferScope): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/plan`,
    { body: scopeBody(scope), errorMessage: '生成转移计划失败' },
  )
  return response.data
}

export async function executeTransfer(jobId: string, scope: TransferScope): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/execute`,
    { body: scopeBody(scope), errorMessage: '执行转移失败' },
  )
  return response.data
}

export async function retryTransfer(jobId: string, scope: TransferScope): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/retry`,
    { body: scopeBody(scope), errorMessage: '重试转移失败' },
  )
  return response.data
}

export async function reconcileTransfer(jobId: string, scope: TransferScope): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/reconcile`,
    { body: scopeBody(scope), errorMessage: '对账转移失败' },
  )
  return response.data
}

export async function cancelTransfer(jobId: string, scope: TransferScope): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/cancel`,
    { body: scopeBody(scope), errorMessage: '取消转移失败' },
  )
  return response.data
}

export async function approveTransfer(jobId: string, input: ApprovalInput): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/approve`,
    { body: input, errorMessage: '审批转移失败' },
  )
  return response.data
}

export async function rejectTransfer(jobId: string, input: ApprovalInput): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/reject`,
    { body: input, errorMessage: '驳回转移失败' },
  )
  return response.data
}

export async function revokeTransferApproval(jobId: string, input: ApprovalInput): Promise<TransferJob> {
  const response = await backendApi.post<{ data: TransferJob }>(
    `${API_BASE}/${encodeURIComponent(jobId)}/revoke`,
    { body: input, errorMessage: '撤销审批失败' },
  )
  return response.data
}
