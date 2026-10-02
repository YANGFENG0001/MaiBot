import { useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRightLeft, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  approveTransfer,
  cancelTransfer,
  createTransfer,
  executeTransfer,
  getTransfer,
  listTransfers,
  planTransfer,
  rejectTransfer,
  retryTransfer,
} from '@/lib/memory-transfers-api'
import { getWorkspaces } from '@/lib/workspaces-api'

import type { TransferCreateInput, TransferJob } from '@/lib/memory-transfers-api'

const EMPTY_CREATE: TransferCreateInput = {
  session_id: '',
  person_id: '',
  audience_type: 'private',
  mode: 'copy',
  source_space_id: '',
  source_partition_ids: [],
  target_space_id: '',
  target_partition_id: '',
  object_types: ['fact'],
  object_ids: [],
  approval_policy: 'manual',
  conflict_policy: 'skip',
}

export function TransfersPanel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [scope, setScope] = useState({ session_id: '', person_id: '', audience_type: 'private' as 'private' | 'group' })
  const [form, setForm] = useState<TransferCreateInput>(EMPTY_CREATE)
  const [selectedJobId, setSelectedJobId] = useState('')

  const spacesQuery = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces })
  const spaces = spacesQuery.data?.memory_spaces ?? []

  const listQuery = useQuery({
    queryKey: ['transfers', scope.session_id, scope.person_id, scope.audience_type],
    queryFn: () => listTransfers(scope),
    enabled: Boolean(scope.session_id && scope.person_id),
  })

  const detailQuery = useQuery({
    queryKey: ['transfer', selectedJobId, scope.session_id, scope.person_id],
    queryFn: () => getTransfer(selectedJobId, scope, true),
    enabled: Boolean(selectedJobId && scope.session_id && scope.person_id),
  })

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['transfers'] }),
      queryClient.invalidateQueries({ queryKey: ['transfer'] }),
    ])
  }

  const createMutation = useMutation({
    mutationFn: () => createTransfer({ ...form, session_id: scope.session_id, person_id: scope.person_id, audience_type: scope.audience_type }),
    onSuccess: async (job) => {
      setSelectedJobId(job.id)
      await refresh()
    },
  })

  const actionMutation = useMutation({
    mutationFn: ({ jobId, action }: { jobId: string; action: 'plan' | 'execute' | 'retry' | 'cancel' }) => {
      if (action === 'plan') return planTransfer(jobId, scope)
      if (action === 'execute') return executeTransfer(jobId, scope)
      if (action === 'retry') return retryTransfer(jobId, scope)
      return cancelTransfer(jobId, scope)
    },
    onSuccess: refresh,
  })

  const approvalMutation = useMutation({
    mutationFn: ({ job, approve }: { job: TransferJob; approve: boolean }) => {
      const payload = {
        ...scope,
        plan_hash: job.plan_hash,
        plan_revision: job.plan_revision,
        policy_revision: 0,
        policy_snapshot_hash: job.policy_snapshot_hash,
        comment: '',
      }
      return approve ? approveTransfer(job.id, payload) : rejectTransfer(job.id, payload)
    },
    onSuccess: refresh,
  })

  const error =
    spacesQuery.error ||
    listQuery.error ||
    detailQuery.error ||
    createMutation.error ||
    actionMutation.error ||
    approvalMutation.error

  const toggleObjectType = (objectType: string) => {
    setForm((current) => {
      const has = current.object_types.includes(objectType)
      return {
        ...current,
        object_types: has
          ? current.object_types.filter((item) => item !== objectType)
          : [...current.object_types, objectType],
      }
    })
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <ArrowRightLeft className="size-5" />
            {t('workspaceAdmin.transfers.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.transfers.subtitle')}</p>
        </div>
        <Button variant="outline" onClick={() => void refresh()}>
          <RefreshCw className="mr-2 size-4" />
          {t('workspaceAdmin.common.refresh')}
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>{t('workspaceAdmin.common.operationFailed')}</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t('workspaceAdmin.transfers.scope')}</CardTitle>
          <CardDescription>{t('workspaceAdmin.transfers.scopeHint')}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="transfer-session">{t('workspaceAdmin.audit.sessionId')}</Label>
            <Input
              id="transfer-session"
              value={scope.session_id}
              onChange={(event) => setScope({ ...scope, session_id: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="transfer-person">{t('workspaceAdmin.audit.personId')}</Label>
            <Input
              id="transfer-person"
              value={scope.person_id}
              onChange={(event) => setScope({ ...scope, person_id: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('workspaceAdmin.audit.audience')}</Label>
            <Select
              value={scope.audience_type}
              onValueChange={(value: 'private' | 'group') => setScope({ ...scope, audience_type: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="z-[100] bg-popover">
                <SelectItem value="private">{t('workspaceAdmin.chatGroups.private')}</SelectItem>
                <SelectItem value="group">{t('workspaceAdmin.chatGroups.group')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t('workspaceAdmin.transfers.create')}</CardTitle>
            <CardDescription>{t('workspaceAdmin.transfers.createHint')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('workspaceAdmin.transfers.mode')}</Label>
                <Select
                  value={form.mode}
                  onValueChange={(value: 'link' | 'copy' | 'promote') => setForm({ ...form, mode: value })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="z-[100] bg-popover">
                    <SelectItem value="link">link</SelectItem>
                    <SelectItem value="copy">copy</SelectItem>
                    <SelectItem value="promote">promote</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('workspaceAdmin.transfers.approvalPolicy')}</Label>
                <Select
                  value={form.approval_policy}
                  onValueChange={(value: 'manual' | 'auto_safe') =>
                    setForm({ ...form, approval_policy: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="z-[100] bg-popover">
                    <SelectItem value="manual">manual</SelectItem>
                    <SelectItem value="auto_safe">auto_safe</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('workspaceAdmin.transfers.sourceSpace')}</Label>
                <Select
                  value={form.source_space_id}
                  onValueChange={(value) => setForm({ ...form, source_space_id: value })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('workspaceAdmin.transfers.pickSpace')} />
                  </SelectTrigger>
                  <SelectContent className="z-[100] bg-popover">
                    {spaces.map((space) => (
                      <SelectItem key={space.id} value={space.id}>
                        {space.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('workspaceAdmin.transfers.targetSpace')}</Label>
                <Select
                  value={form.target_space_id}
                  onValueChange={(value) => setForm({ ...form, target_space_id: value })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('workspaceAdmin.transfers.pickSpace')} />
                  </SelectTrigger>
                  <SelectContent className="z-[100] bg-popover">
                    {spaces.map((space) => (
                      <SelectItem key={space.id} value={space.id}>
                        {space.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="target-partition">{t('workspaceAdmin.transfers.targetPartition')}</Label>
                <Input
                  id="target-partition"
                  value={form.target_partition_id}
                  onChange={(event) => setForm({ ...form, target_partition_id: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="source-partitions">{t('workspaceAdmin.transfers.sourcePartitions')}</Label>
                <Input
                  id="source-partitions"
                  value={form.source_partition_ids.join(', ')}
                  placeholder="memory-partition-…"
                  onChange={(event) =>
                    setForm({
                      ...form,
                      source_partition_ids: event.target.value
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {['fact', 'episode', 'profile', 'relationship', 'summary'].map((type) => (
                <Button
                  key={type}
                  type="button"
                  size="sm"
                  variant={form.object_types.includes(type) ? 'default' : 'outline'}
                  onClick={() => toggleObjectType(type)}
                >
                  {type}
                </Button>
              ))}
            </div>
            <Button
              disabled={
                !scope.session_id ||
                !scope.person_id ||
                !form.source_space_id ||
                !form.target_space_id ||
                !form.target_partition_id ||
                !form.source_partition_ids.length ||
                createMutation.isPending
              }
              onClick={() => createMutation.mutate()}
            >
              {t('workspaceAdmin.transfers.create')}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t('workspaceAdmin.transfers.jobs')}</CardTitle>
            <CardDescription>{t('workspaceAdmin.transfers.jobsHint')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {(listQuery.data ?? []).map((job) => (
              <button
                key={job.id}
                type="button"
                className={`w-full rounded-lg border p-3 text-left text-sm transition-colors ${
                  selectedJobId === job.id ? 'border-primary bg-primary/5' : 'hover:bg-accent/50'
                }`}
                onClick={() => setSelectedJobId(job.id)}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{job.mode}</Badge>
                  <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{job.status}</Badge>
                  <Badge variant="outline">{job.approval_state}</Badge>
                </div>
                <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">{job.id}</div>
                <div className="text-xs text-muted-foreground">
                  {new Date(job.created_at).toLocaleString()}
                  {job.last_error_code ? ` · ${job.last_error_code}` : ''}
                </div>
              </button>
            ))}
            {!scope.session_id || !scope.person_id ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                {t('workspaceAdmin.transfers.needScope')}
              </div>
            ) : null}
            {scope.session_id && scope.person_id && !listQuery.data?.length && (
              <div className="py-6 text-center text-sm text-muted-foreground">
                {t('workspaceAdmin.common.noRecords')}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {detailQuery.data && (
        <Card>
          <CardHeader>
            <CardTitle>{t('workspaceAdmin.transfers.detail')}</CardTitle>
            <CardDescription className="font-mono text-xs">{detailQuery.data.job.id}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => actionMutation.mutate({ jobId: detailQuery.data.job.id, action: 'plan' })}
              >
                {t('workspaceAdmin.transfers.plan')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => actionMutation.mutate({ jobId: detailQuery.data.job.id, action: 'execute' })}
              >
                {t('workspaceAdmin.transfers.execute')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => actionMutation.mutate({ jobId: detailQuery.data.job.id, action: 'retry' })}
              >
                {t('workspaceAdmin.transfers.retry')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  approvalMutation.mutate({ job: detailQuery.data.job, approve: true })
                }
              >
                {t('workspaceAdmin.transfers.approve')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  approvalMutation.mutate({ job: detailQuery.data.job, approve: false })
                }
              >
                {t('workspaceAdmin.transfers.reject')}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => actionMutation.mutate({ jobId: detailQuery.data.job.id, action: 'cancel' })}
              >
                {t('workspaceAdmin.transfers.cancel')}
              </Button>
            </div>
            <div className="grid gap-2 md:grid-cols-3 text-xs">
              <div className="rounded-lg border p-2">
                <div className="text-muted-foreground">{t('workspaceAdmin.transfers.planHash')}</div>
                <div className="mt-1 font-mono break-all">{detailQuery.data.job.plan_hash}</div>
              </div>
              <div className="rounded-lg border p-2">
                <div className="text-muted-foreground">{t('workspaceAdmin.transfers.planRevision')}</div>
                <div className="mt-1 font-medium">{detailQuery.data.job.plan_revision}</div>
              </div>
              <div className="rounded-lg border p-2">
                <div className="text-muted-foreground">{t('workspaceAdmin.transfers.retryCount')}</div>
                <div className="mt-1 font-medium">
                  {detailQuery.data.job.retry_count} / {detailQuery.data.job.max_retries}
                </div>
              </div>
            </div>
            <div className="space-y-2">
              {detailQuery.data.items.map((item) => (
                <div key={item.id} className="rounded-lg border p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{item.object_type}</Badge>
                    <Badge variant={item.status === 'failed' ? 'destructive' : 'secondary'}>{item.status}</Badge>
                    {item.conflict_code && <Badge variant="destructive">{item.conflict_code}</Badge>}
                    {item.error_code && <Badge variant="destructive">{item.error_code}</Badge>}
                  </div>
                  <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                    {item.source_partition_id} → {item.target_partition_id}
                  </div>
                </div>
              ))}
              {!detailQuery.data.items.length && (
                <div className="py-4 text-center text-sm text-muted-foreground">
                  {t('workspaceAdmin.common.noRecords')}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
