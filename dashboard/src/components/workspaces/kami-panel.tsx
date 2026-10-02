import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw, ShieldOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { getKamiProfile, listKamiSessions, revokeKamiSession, updateKamiProfile } from '@/lib/memory-audit-api'

export function KamiPanel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const profileQuery = useQuery({ queryKey: ['kami-profile'], queryFn: getKamiProfile })
  const sessionsQuery = useQuery({ queryKey: ['kami-sessions'], queryFn: () => listKamiSessions() })

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['kami-profile'] }),
      queryClient.invalidateQueries({ queryKey: ['kami-sessions'] }),
    ])
  }

  const updateMutation = useMutation({
    mutationFn: (input: Parameters<typeof updateKamiProfile>[0]) =>
      updateKamiProfile({ ...input, expected_revision: profileQuery.data?.policy_revision }),
    onSuccess: refresh,
  })

  const revokeMutation = useMutation({
    mutationFn: (stateId: string) => revokeKamiSession(stateId),
    onSuccess: refresh,
  })

  const profile = profileQuery.data
  const error = profileQuery.error || sessionsQuery.error || updateMutation.error || revokeMutation.error

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold text-destructive">
            <AlertTriangle className="size-5" />
            {t('workspaceAdmin.kami.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.kami.subtitle')}</p>
        </div>
        <Button variant="outline" onClick={() => void refresh()}>
          <RefreshCw className="mr-2 size-4" />
          {t('workspaceAdmin.common.refresh')}
        </Button>
      </div>

      <Alert variant="destructive">
        <AlertTitle>{t('workspaceAdmin.kami.dangerTitle')}</AlertTitle>
        <AlertDescription>{t('workspaceAdmin.kami.dangerBody')}</AlertDescription>
      </Alert>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>{t('workspaceAdmin.common.operationFailed')}</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle>{t('workspaceAdmin.kami.profile')}</CardTitle>
            <CardDescription>{t('workspaceAdmin.kami.profileHint')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {profile && (
              <>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.kami.enabled')}</div>
                    <div className="text-xs text-muted-foreground">{t('workspaceAdmin.kami.enabledHint')}</div>
                  </div>
                  <Switch
                    checked={profile.enabled}
                    onCheckedChange={(checked) => updateMutation.mutate({ enabled: checked })}
                  />
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-lg border p-3">
                    <div className="text-xs text-muted-foreground">{t('workspaceAdmin.kami.homeSpace')}</div>
                    <div className="mt-1 font-mono text-xs break-all">{profile.home_memory_space_id}</div>
                  </div>
                  <div className="rounded-lg border p-3">
                    <div className="text-xs text-muted-foreground">{t('workspaceAdmin.kami.ttl')}</div>
                    <div className="mt-1 font-medium">
                      {profile.default_ttl_seconds}s / max {profile.max_ttl_seconds}s
                    </div>
                  </div>
                  <div className="rounded-lg border p-3">
                    <div className="text-xs text-muted-foreground">{t('workspaceAdmin.botProfiles.persona')}</div>
                    <div className="mt-1 font-medium">
                      {profile.persona_profile_id || t('workspaceAdmin.common.none')}
                    </div>
                  </div>
                  <div className="rounded-lg border p-3">
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.permissionGroups.revisionLabel')}
                    </div>
                    <div className="mt-1 font-medium">{profile.policy_revision}</div>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle>{t('workspaceAdmin.kami.sessions')}</CardTitle>
            <CardDescription>{t('workspaceAdmin.kami.sessionsHint')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {(sessionsQuery.data ?? []).map((session) => (
              <div
                key={session.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge variant={session.status === 'active' ? 'destructive' : 'outline'}>
                      {session.status}
                    </Badge>
                    {session.expired && session.status === 'active' && (
                      <Badge variant="secondary">{t('workspaceAdmin.kami.expired')}</Badge>
                    )}
                  </div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">
                    {session.person_id} · {session.session_id}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {t('workspaceAdmin.kami.expiresAt')}: {new Date(session.expires_at).toLocaleString()}
                  </div>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={session.status !== 'active' || revokeMutation.isPending}
                  onClick={() => revokeMutation.mutate(session.id)}
                >
                  <ShieldOff className="mr-1 size-4" />
                  {t('workspaceAdmin.kami.revoke')}
                </Button>
              </div>
            ))}
            {!sessionsQuery.data?.length && (
              <div className="py-6 text-center text-sm text-muted-foreground">
                {t('workspaceAdmin.kami.noSessions')}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
