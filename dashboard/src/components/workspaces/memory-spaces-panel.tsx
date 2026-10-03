import { useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, HardDrive, Plus, RefreshCw, ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  createMemorySpace,
  getMemorySpaceAcl,
  getMemorySpaceBotAccess,
  getMemorySpacePartitions,
  getWorkspaces,
  migrateLegacyMemoryGroups,
  setMemorySpaceAcl,
  updateMemorySpace,
} from '@/lib/workspaces-api'

import type { MemorySpaceAclItem } from '@/lib/workspaces-api'

export function MemorySpacesPanel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selectedSpaceId, setSelectedSpaceId] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', description: '', strict_isolation: false })

  const workspaceQuery = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces })
  const spaces = useMemo(() => workspaceQuery.data?.memory_spaces ?? [], [workspaceQuery.data])
  const selectedSpace = useMemo(
    () => spaces.find((item) => item.id === selectedSpaceId) ?? spaces[0],
    [selectedSpaceId, spaces],
  )
  const activeSpaceId = selectedSpace?.id ?? ''

  const partitionQuery = useQuery({
    queryKey: ['memory-space-partitions', activeSpaceId],
    queryFn: () => getMemorySpacePartitions(activeSpaceId),
    enabled: Boolean(activeSpaceId),
  })
  const aclQuery = useQuery({
    queryKey: ['memory-space-acl', activeSpaceId],
    queryFn: () => getMemorySpaceAcl(activeSpaceId),
    enabled: Boolean(activeSpaceId),
  })
  const botAccessQuery = useQuery({
    queryKey: ['memory-space-bot-access', activeSpaceId],
    queryFn: () => getMemorySpaceBotAccess(activeSpaceId),
    enabled: Boolean(activeSpaceId),
  })

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-space-partitions'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-space-acl'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-space-bot-access'] }),
    ])
  }

  const createMutation = useMutation({
    mutationFn: () => createMemorySpace({ ...createForm, space_type: 'private' }),
    onSuccess: async (space) => {
      setSelectedSpaceId(space.id)
      setCreateOpen(false)
      setCreateForm({ name: '', description: '', strict_isolation: false })
      await refresh()
    },
  })

  const aclMutation = useMutation({
    mutationFn: ({
      peerSpaceId,
      input,
    }: {
      peerSpaceId: string
      input: Pick<MemorySpaceAclItem, 'can_read_from_peer' | 'expose_to_peer'>
    }) => setMemorySpaceAcl(activeSpaceId, peerSpaceId, input),
    onSuccess: refresh,
  })

  const isolationMutation = useMutation({
    mutationFn: (strict: boolean) => updateMemorySpace(activeSpaceId, { strict_isolation: strict }),
    onSuccess: refresh,
  })

  const migrateMutation = useMutation({ mutationFn: migrateLegacyMemoryGroups, onSuccess: refresh })

  const error =
    workspaceQuery.error ||
    partitionQuery.error ||
    aclQuery.error ||
    botAccessQuery.error ||
    createMutation.error ||
    aclMutation.error ||
    isolationMutation.error ||
    migrateMutation.error

  const partitions = partitionQuery.data?.data ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Database className="size-5" />
            {t('workspaceAdmin.memorySpaces.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.memorySpaces.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void refresh()}>
            <RefreshCw className="mr-2 size-4" />
            {t('workspaceAdmin.common.refresh')}
          </Button>
          <Button variant="outline" onClick={() => migrateMutation.mutate()} disabled={migrateMutation.isPending}>
            {t('workspaceAdmin.memorySpaces.migrateLegacy')}
          </Button>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="mr-2 size-4" />
                {t('workspaceAdmin.memorySpaces.create')}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t('workspaceAdmin.memorySpaces.create')}</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="memory-space-name">{t('workspaceAdmin.common.name')}</Label>
                  <Input
                    id="memory-space-name"
                    value={createForm.name}
                    onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="memory-space-description">{t('workspaceAdmin.common.description')}</Label>
                  <Textarea
                    id="memory-space-description"
                    value={createForm.description}
                    onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })}
                  />
                </div>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.memorySpaces.strictIsolation')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.memorySpaces.strictIsolationHint')}
                    </div>
                  </div>
                  <Switch
                    checked={createForm.strict_isolation}
                    onCheckedChange={(checked) => setCreateForm({ ...createForm, strict_isolation: checked })}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setCreateOpen(false)}>
                  {t('workspaceAdmin.common.cancel')}
                </Button>
                <Button
                  disabled={!createForm.name.trim() || createMutation.isPending}
                  onClick={() => createMutation.mutate()}
                >
                  {t('workspaceAdmin.common.create')}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>{t('workspaceAdmin.common.operationFailed')}</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-3">
          {spaces.map((space) => (
            <button
              key={space.id}
              type="button"
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                selectedSpace?.id === space.id ? 'border-primary bg-primary/5' : 'bg-card hover:bg-accent/50'
              }`}
              onClick={() => setSelectedSpaceId(space.id)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="font-semibold">{space.name}</div>
                <Badge variant={space.space_type === 'public' ? 'default' : 'outline'}>
                  {space.space_type === 'public'
                    ? t('workspaceAdmin.memorySpaces.typePublic')
                    : space.space_type === 'kami'
                      ? t('workspaceAdmin.memorySpaces.typeKami')
                      : t('workspaceAdmin.memorySpaces.typePrivate')}
                </Badge>
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {space.description || t('workspaceAdmin.common.noDescription')}
              </div>
              {space.strict_isolation && (
                <Badge variant="destructive" className="mt-2">
                  <ShieldAlert className="mr-1 size-3" />
                  {t('workspaceAdmin.memorySpaces.strictIsolation')}
                </Badge>
              )}
            </button>
          ))}
        </div>

        {selectedSpace && (
          <div className="space-y-5">
            <Card>
              <CardHeader>
                <CardTitle>{selectedSpace.name}</CardTitle>
                <CardDescription>
                  {t('workspaceAdmin.memorySpaces.revision', { revision: selectedSpace.policy_revision })}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.memorySpaces.strictIsolation')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.memorySpaces.strictIsolationHint')}
                    </div>
                  </div>
                  <Switch
                    checked={selectedSpace.strict_isolation}
                    onCheckedChange={(checked) => isolationMutation.mutate(checked)}
                  />
                </div>

                <div>
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                    <HardDrive className="size-4" />
                    {t('workspaceAdmin.memorySpaces.partitions')}
                  </div>
                  <div className="space-y-2">
                    {partitions.map((partition) => (
                      <div
                        key={partition.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                      >
                        <div className="flex items-center gap-2">
                          <Badge variant="outline">{partition.partition_type}</Badge>
                          <span className="font-mono text-xs break-all">{partition.partition_key}</span>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant="secondary">{partition.security_domain}</Badge>
                          <span>{t('workspaceAdmin.memorySpaces.objectCount', { count: partition.object_count })}</span>
                        </div>
                      </div>
                    ))}
                    {!partitions.length && (
                      <div className="py-6 text-center text-sm text-muted-foreground">
                        {t('workspaceAdmin.memorySpaces.noPartitions')}
                      </div>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.memorySpaces.botAccess')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.memorySpaces.botAccessHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {(botAccessQuery.data ?? []).map((row) => {
                  const allowed = row.outbound_can_read !== false && row.inbound_can_read !== false
                  return (
                    <div
                      key={row.bot_profile_id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{row.bot_profile_name}</span>
                        <Badge variant="outline">{row.profile_type}</Badge>
                      </div>
                      <Badge variant={allowed ? 'secondary' : 'destructive'}>
                        {allowed
                          ? t('workspaceAdmin.memorySpaces.allowed')
                          : t('workspaceAdmin.memorySpaces.denied')}
                      </Badge>
                    </div>
                  )
                })}
                {!botAccessQuery.data?.length && (
                  <div className="py-6 text-center text-sm text-muted-foreground">
                    {t('workspaceAdmin.memorySpaces.noBotAccess')}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.memorySpaces.acl')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.memorySpaces.aclHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {spaces
                  .filter((space) => space.id !== selectedSpace.id)
                  .map((space) => {
                    const acl = aclQuery.data?.find((item) => item.peer_space_id === space.id)
                    const current = {
                      can_read_from_peer: acl?.can_read_from_peer ?? false,
                      expose_to_peer: acl?.expose_to_peer ?? false,
                    }
                    return (
                      <div
                        key={space.id}
                        className="grid gap-3 rounded-lg border p-3 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center"
                      >
                        <div>
                          <div className="font-medium">{space.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {space.description || t('workspaceAdmin.common.noDescription')}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 text-sm">
                          <Switch
                            aria-label={`${selectedSpace.name} → ${space.name}`}
                            checked={current.can_read_from_peer}
                            onCheckedChange={(checked) =>
                              aclMutation.mutate({
                                peerSpaceId: space.id,
                                input: { ...current, can_read_from_peer: checked },
                              })
                            }
                          />
                          {t('workspaceAdmin.memorySpaces.canRead')}
                        </div>
                        <div className="flex items-center gap-2 text-sm">
                          <Switch
                            aria-label={`${selectedSpace.name} ← ${space.name}`}
                            checked={current.expose_to_peer}
                            onCheckedChange={(checked) =>
                              aclMutation.mutate({
                                peerSpaceId: space.id,
                                input: { ...current, expose_to_peer: checked },
                              })
                            }
                          />
                          {t('workspaceAdmin.memorySpaces.canExpose')}
                        </div>
                      </div>
                    )
                  })}
                {spaces.length <= 1 && (
                  <div className="py-6 text-center text-sm text-muted-foreground">
                    {t('workspaceAdmin.memorySpaces.noPeerSpaces')}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
