import { useState } from 'react'

import { useMutation, useQuery } from '@tanstack/react-query'
import { Eye, EyeOff, RefreshCw, ShieldQuestion } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { getBotControlAudit, getMemoryAccessAudit } from '@/lib/memory-audit-api'
import { getBotProfiles } from '@/lib/bot-profiles-api'
import { simulateAccess } from '@/lib/memory-permissions-api'

import type { SimulateResult } from '@/lib/memory-permissions-api'

function ScopeList({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex flex-wrap gap-1">
        {values.map((value) => (
          <Badge key={value} variant="outline" className="font-mono text-[10px]">
            {value}
          </Badge>
        ))}
      </div>
    </div>
  )
}

export function AuditPanel() {
  const { t } = useTranslation()
  const [reveal, setReveal] = useState(false)
  const [simForm, setSimForm] = useState({
    session_id: '',
    person_id: '',
    audience_type: 'private' as 'private' | 'group',
    bot_profile_id: '',
  })
  const [simResult, setSimResult] = useState<SimulateResult | null>(null)

  const accessAuditQuery = useQuery({
    queryKey: ['memory-access-audit', reveal],
    queryFn: () => getMemoryAccessAudit({ reveal }),
  })
  const controlAuditQuery = useQuery({
    queryKey: ['bot-control-audit', reveal],
    queryFn: () => getBotControlAudit({ reveal }),
  })
  const profileQuery = useQuery({ queryKey: ['bot-profiles'], queryFn: getBotProfiles })

  const simulateMutation = useMutation({
    mutationFn: () => simulateAccess({ ...simForm, bot_profile_id: simForm.bot_profile_id }),
    onSuccess: (result) => setSimResult(result),
  })

  const error = accessAuditQuery.error || controlAuditQuery.error || simulateMutation.error

  const refresh = async () => {
    await Promise.all([
      accessAuditQuery.refetch(),
      controlAuditQuery.refetch(),
    ])
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <ShieldQuestion className="size-5" />
            {t('workspaceAdmin.audit.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.audit.subtitle')}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-sm">
            {reveal ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
            <Switch checked={reveal} onCheckedChange={setReveal} aria-label={t('workspaceAdmin.audit.reveal')} />
            {t('workspaceAdmin.audit.reveal')}
          </div>
          <Button variant="outline" onClick={() => void refresh()}>
            <RefreshCw className="mr-2 size-4" />
            {t('workspaceAdmin.common.refresh')}
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>{t('workspaceAdmin.common.operationFailed')}</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t('workspaceAdmin.audit.simulator')}</CardTitle>
          <CardDescription>{t('workspaceAdmin.audit.simulatorHint')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-4">
            <div className="space-y-1">
              <Label htmlFor="sim-session">{t('workspaceAdmin.audit.sessionId')}</Label>
              <Input
                id="sim-session"
                value={simForm.session_id}
                onChange={(event) => setSimForm({ ...simForm, session_id: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="sim-person">{t('workspaceAdmin.audit.personId')}</Label>
              <Input
                id="sim-person"
                value={simForm.person_id}
                onChange={(event) => setSimForm({ ...simForm, person_id: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('workspaceAdmin.audit.audience')}</Label>
              <Select
                value={simForm.audience_type}
                onValueChange={(value: 'private' | 'group') => setSimForm({ ...simForm, audience_type: value })}
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
            <div className="space-y-1">
              <Label>{t('workspaceAdmin.audit.botProfile')}</Label>
              <Select
                value={simForm.bot_profile_id || '__auto__'}
                onValueChange={(value) =>
                  setSimForm({ ...simForm, bot_profile_id: value === '__auto__' ? '' : value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="z-[100] bg-popover">
                  <SelectItem value="__auto__">{t('workspaceAdmin.audit.autoResolve')}</SelectItem>
                  {(profileQuery.data ?? []).map((profile) => (
                    <SelectItem key={profile.id} value={profile.id}>
                      {profile.name} ({profile.profile_type})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <Button
            disabled={!simForm.session_id.trim() || !simForm.person_id.trim() || simulateMutation.isPending}
            onClick={() => simulateMutation.mutate()}
          >
            {t('workspaceAdmin.audit.runSimulation')}
          </Button>

          {simResult && (
            <div className="space-y-3 rounded-lg border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={simResult.allowed ? 'secondary' : 'destructive'}>
                  {simResult.allowed ? t('workspaceAdmin.audit.allowed') : t('workspaceAdmin.audit.denied')}
                </Badge>
                <Badge variant="outline">{simResult.access_mode}</Badge>
                <Badge variant="outline">{simResult.security_domain}</Badge>
                {simResult.permission_group_name && <Badge>{simResult.permission_group_name}</Badge>}
              </div>
              {!simResult.allowed && simResult.denied_reason && (
                <div className="text-sm text-destructive">{simResult.denied_reason}</div>
              )}
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border p-3 text-xs">
                  <div className="text-muted-foreground">{t('workspaceAdmin.audit.workspace')}</div>
                  <div className="mt-1 font-medium">
                    {simResult.workspace_name} · {simResult.workspace_id}
                  </div>
                  <div className="mt-2 text-muted-foreground">{t('workspaceAdmin.audit.activeBot')}</div>
                  <div className="mt-1 font-medium">
                    {simResult.bot_profile_name} ({simResult.active_bot_profile_type})
                  </div>
                  <div className="mt-2 text-muted-foreground">{t('workspaceAdmin.audit.traceId')}</div>
                  <div className="mt-1 font-mono text-[10px] break-all">{simResult.trace_id}</div>
                </div>
                <div className="space-y-2 rounded-lg border p-3">
                  <ScopeList
                    label={t('workspaceAdmin.audit.requestedScope')}
                    values={simResult.requested_scope.partition_ids}
                  />
                  <ScopeList
                    label={t('workspaceAdmin.audit.allowedScope')}
                    values={simResult.allowed_scope.partition_ids}
                  />
                  <ScopeList
                    label={t('workspaceAdmin.audit.deniedScope')}
                    values={simResult.denied_scope.partition_ids}
                  />
                  <ScopeList
                    label={t('workspaceAdmin.audit.writableScope')}
                    values={simResult.writable_partition_ids}
                  />
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                {simResult.capabilities.map((capability) => (
                  <Badge key={capability} variant="outline" className="font-mono text-[10px]">
                    {capability}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('workspaceAdmin.audit.memoryAccess')}</CardTitle>
          <CardDescription>{t('workspaceAdmin.audit.memoryAccessHint')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {(accessAuditQuery.data?.data ?? []).map((row) => (
            <div key={row.id} className="rounded-lg border p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={row.access_mode === 'forced_kami' ? 'destructive' : 'outline'}>
                  {row.access_mode}
                </Badge>
                <span className="text-muted-foreground">{new Date(row.created_at).toLocaleString()}</span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {t('workspaceAdmin.audit.queryHash')}: {row.query_hash}
                </span>
                <span className="text-muted-foreground">
                  {t('workspaceAdmin.audit.resultCount', { count: row.result_count })}
                </span>
                {row.redacted && <Badge variant="secondary">{t('workspaceAdmin.audit.redacted')}</Badge>}
              </div>
              <div className="mt-1 truncate text-muted-foreground">
                {row.person_id} · {row.session_id} · {row.permission_group_id || t('workspaceAdmin.common.none')}
              </div>
            </div>
          ))}
          {!accessAuditQuery.data?.data.length && (
            <div className="py-6 text-center text-sm text-muted-foreground">
              {t('workspaceAdmin.common.noRecords')}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('workspaceAdmin.audit.botControl')}</CardTitle>
          <CardDescription>{t('workspaceAdmin.audit.botControlHint')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {(controlAuditQuery.data?.data ?? []).map((row) => (
            <div key={row.id} className="rounded-lg border p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={row.command.includes('kami') ? 'destructive' : 'outline'}>{row.command}</Badge>
                <Badge variant={row.result === 'success' ? 'secondary' : 'destructive'}>{row.result}</Badge>
                <span className="text-muted-foreground">{new Date(row.created_at).toLocaleString()}</span>
                <span className="text-muted-foreground">{row.reason}</span>
                {row.redacted && <Badge variant="secondary">{t('workspaceAdmin.audit.redacted')}</Badge>}
              </div>
              <div className="mt-1 truncate text-muted-foreground">
                {row.person_id} · {row.session_id} · {row.platform}
              </div>
            </div>
          ))}
          {!controlAuditQuery.data?.data.length && (
            <div className="py-6 text-center text-sm text-muted-foreground">
              {t('workspaceAdmin.common.noRecords')}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
