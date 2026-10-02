import { useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, ShieldCheck, Trash2, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  addGroupContext,
  addGroupMember,
  addGroupRule,
  createPermissionGroup,
  deleteGroupRule,
  deletePermissionGroup,
  getPermissionGroup,
  getPermissionGroups,
  listPersonOptions,
  removeGroupBotRule,
  removeGroupCapability,
  removeGroupContext,
  removeGroupMember,
  setGroupBotRule,
  setGroupCapability,
  updatePermissionGroup,
} from '@/lib/memory-permissions-api'

import type { PermissionGroupItem, RuleCreateInput } from '@/lib/memory-permissions-api'

const EMPTY_RULE: RuleCreateInput = {
  effect: 'allow',
  space_selector: 'current',
  partition_type: 'any',
  partition_selector: 'any',
}

export function PermissionGroupsPanel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState({
    name: '',
    description: '',
    priority: 0,
    memory_scope_mode: 'inherit' as 'inherit' | 'override',
    is_manager_mode: false,
  })
  const [memberPersonId, setMemberPersonId] = useState('')
  const [contextForm, setContextForm] = useState<{
    scope_type: 'global' | 'workspace' | 'session' | 'channel'
    workspace_id: string
    session_id: string
    channel_type: 'private' | 'group'
    allow_group_disclosure: boolean
  }>({
    scope_type: 'global',
    workspace_id: '',
    session_id: '',
    channel_type: 'private',
    allow_group_disclosure: false,
  })
  const [ruleForm, setRuleForm] = useState<RuleCreateInput>(EMPTY_RULE)
  const [botRuleForm, setBotRuleForm] = useState<{
    effect: 'allow' | 'deny'
    bot_selector: 'public' | 'current_group' | 'kami' | 'specific'
    bot_profile_id: string
  }>({ effect: 'allow', bot_selector: 'current_group', bot_profile_id: '' })

  const groupsQuery = useQuery({ queryKey: ['permission-groups'], queryFn: getPermissionGroups })
  const groups = useMemo(() => groupsQuery.data?.data ?? [], [groupsQuery.data?.data])
  const catalog = groupsQuery.data
  const selected: PermissionGroupItem | undefined = useMemo(
    () => groups.find((item) => item.id === selectedId) ?? groups[0],
    [selectedId, groups],
  )
  const activeGroupId = selected?.id ?? ''

  const detailQuery = useQuery({
    queryKey: ['permission-group', activeGroupId],
    queryFn: () => getPermissionGroup(activeGroupId),
    enabled: Boolean(activeGroupId),
  })
  const personQuery = useQuery({ queryKey: ['permission-persons'], queryFn: listPersonOptions })

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['permission-groups'] }),
      queryClient.invalidateQueries({ queryKey: ['permission-group'] }),
    ])
  }

  const revision = detailQuery.data?.data.policy_revision ?? selected?.policy_revision

  const createMutation = useMutation({
    mutationFn: () => createPermissionGroup(createForm),
    onSuccess: async (group) => {
      setSelectedId(group.id)
      setCreateOpen(false)
      setCreateForm({ name: '', description: '', priority: 0, memory_scope_mode: 'inherit', is_manager_mode: false })
      await refresh()
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (groupId: string) => deletePermissionGroup(groupId),
    onSuccess: async () => {
      setSelectedId('')
      await refresh()
    },
  })

  const toggleEnabledMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      updatePermissionGroup(activeGroupId, { enabled, expected_revision: revision }),
    onSuccess: refresh,
  })

  const memberMutation = useMutation({
    mutationFn: () => addGroupMember(activeGroupId, memberPersonId, revision),
    onSuccess: async () => {
      setMemberPersonId('')
      await refresh()
    },
  })
  const removeMemberMutation = useMutation({
    mutationFn: (personId: string) => removeGroupMember(activeGroupId, personId),
    onSuccess: refresh,
  })

  const contextMutation = useMutation({
    mutationFn: () =>
      addGroupContext(activeGroupId, {
        scope_type: contextForm.scope_type,
        workspace_id: contextForm.workspace_id || null,
        session_id: contextForm.session_id || null,
        channel_type: contextForm.scope_type === 'channel' ? contextForm.channel_type : null,
        allow_group_disclosure: contextForm.allow_group_disclosure,
        expected_revision: revision,
      }),
    onSuccess: refresh,
  })
  const removeContextMutation = useMutation({
    mutationFn: (contextId: number) => removeGroupContext(activeGroupId, contextId),
    onSuccess: refresh,
  })

  const capabilityMutation = useMutation({
    mutationFn: ({ capability, enabled }: { capability: string; enabled: boolean }) =>
      setGroupCapability(activeGroupId, capability, enabled, revision),
    onSuccess: refresh,
  })
  const removeCapabilityMutation = useMutation({
    mutationFn: (capability: string) => removeGroupCapability(activeGroupId, capability),
    onSuccess: refresh,
  })

  const ruleMutation = useMutation({
    mutationFn: () => addGroupRule(activeGroupId, ruleForm),
    onSuccess: async () => {
      setRuleForm(EMPTY_RULE)
      await refresh()
    },
  })
  const deleteRuleMutation = useMutation({
    mutationFn: (ruleId: number) => deleteGroupRule(activeGroupId, ruleId),
    onSuccess: refresh,
  })

  const botRuleMutation = useMutation({
    mutationFn: () =>
      setGroupBotRule(activeGroupId, {
        effect: botRuleForm.effect,
        bot_selector: botRuleForm.bot_selector,
        bot_profile_id: botRuleForm.bot_selector === 'specific' ? botRuleForm.bot_profile_id : null,
        expected_revision: revision,
      }),
    onSuccess: refresh,
  })
  const removeBotRuleMutation = useMutation({
    mutationFn: (botRuleId: number) => removeGroupBotRule(activeGroupId, botRuleId),
    onSuccess: refresh,
  })

  const error =
    groupsQuery.error ||
    detailQuery.error ||
    personQuery.error ||
    createMutation.error ||
    deleteMutation.error ||
    toggleEnabledMutation.error ||
    memberMutation.error ||
    removeMemberMutation.error ||
    contextMutation.error ||
    removeContextMutation.error ||
    capabilityMutation.error ||
    removeCapabilityMutation.error ||
    ruleMutation.error ||
    deleteRuleMutation.error ||
    botRuleMutation.error ||
    removeBotRuleMutation.error

  const detail = detailQuery.data
  const enabledCapabilities = new Set(
    (detail?.capabilities ?? []).filter((item) => item.enabled).map((item) => item.capability),
  )

  if (groupsQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">{t('workspaceAdmin.common.loading')}</div>
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <ShieldCheck className="size-5" />
            {t('workspaceAdmin.permissionGroups.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.permissionGroups.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void refresh()}>
            <RefreshCw className="mr-2 size-4" />
            {t('workspaceAdmin.common.refresh')}
          </Button>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="mr-2 size-4" />
                {t('workspaceAdmin.permissionGroups.create')}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t('workspaceAdmin.permissionGroups.create')}</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="group-name">{t('workspaceAdmin.common.name')}</Label>
                  <Input
                    id="group-name"
                    value={createForm.name}
                    onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="group-description">{t('workspaceAdmin.common.description')}</Label>
                  <Textarea
                    id="group-description"
                    value={createForm.description}
                    onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t('workspaceAdmin.permissionGroups.scopeMode')}</Label>
                  <Select
                    value={createForm.memory_scope_mode}
                    onValueChange={(value: 'inherit' | 'override') =>
                      setCreateForm({ ...createForm, memory_scope_mode: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      <SelectItem value="inherit">
                        {t('workspaceAdmin.permissionGroups.scopeInherit')}
                      </SelectItem>
                      <SelectItem value="override">
                        {t('workspaceAdmin.permissionGroups.scopeOverride')}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.permissionGroups.managerMode')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.permissionGroups.managerModeHint')}
                    </div>
                  </div>
                  <Switch
                    checked={createForm.is_manager_mode}
                    onCheckedChange={(checked) => setCreateForm({ ...createForm, is_manager_mode: checked })}
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
          {groups.map((group) => (
            <button
              key={group.id}
              type="button"
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                selected?.id === group.id ? 'border-primary bg-primary/5' : 'bg-card hover:bg-accent/50'
              }`}
              onClick={() => setSelectedId(group.id)}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="font-semibold">{group.name}</div>
                {group.is_manager_mode && (
                  <Badge variant="destructive">{t('workspaceAdmin.permissionGroups.managerMode')}</Badge>
                )}
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {group.description || t('workspaceAdmin.common.noDescription')}
              </div>
              <div className="mt-2 flex flex-wrap gap-1 text-xs">
                <Badge variant="outline">
                  <Users className="mr-1 size-3" />
                  {group.counts.members}
                </Badge>
                <Badge variant="outline">{t('workspaceAdmin.permissionGroups.rulesCount', { count: group.counts.rules })}</Badge>
                <Badge variant="outline">
                  {t('workspaceAdmin.permissionGroups.capabilitiesCount', { count: group.counts.capabilities })}
                </Badge>
                {!group.enabled && <Badge variant="destructive">{t('workspaceAdmin.common.disabled')}</Badge>}
              </div>
            </button>
          ))}
          {!groups.length && (
            <div className="rounded-xl border p-6 text-center text-sm text-muted-foreground">
              {t('workspaceAdmin.permissionGroups.empty')}
            </div>
          )}
        </div>

        {selected && detail && (
          <div className="space-y-5">
            <Card>
              <CardHeader className="flex flex-row items-start justify-between gap-3">
                <div>
                  <CardTitle>{selected.name}</CardTitle>
                  <CardDescription>
                    {t('workspaceAdmin.permissionGroups.revision', { revision: detail.data.policy_revision })}
                  </CardDescription>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={selected.enabled}
                      onCheckedChange={(checked) => toggleEnabledMutation.mutate(checked)}
                    />
                    {t('workspaceAdmin.common.enabled')}
                  </div>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => deleteMutation.mutate(selected.id)}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="mr-1 size-4" />
                    {t('workspaceAdmin.common.delete')}
                  </Button>
                </div>
              </CardHeader>
              {selected.is_manager_mode && (
                <CardContent>
                  <Alert>
                    <AlertTitle>{t('workspaceAdmin.permissionGroups.managerPresetTitle')}</AlertTitle>
                    <AlertDescription>
                      {t('workspaceAdmin.permissionGroups.managerPresetBody', {
                        capabilities: (catalog?.manager_preset_capabilities ?? []).join(' + '),
                      })}
                    </AlertDescription>
                  </Alert>
                </CardContent>
              )}
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.permissionGroups.members')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.permissionGroups.membersHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[260px] flex-1 space-y-1">
                    <Label htmlFor="member-person">{t('workspaceAdmin.permissionGroups.personPicker')}</Label>
                    <Select value={memberPersonId} onValueChange={setMemberPersonId}>
                      <SelectTrigger id="member-person">
                        <SelectValue placeholder={t('workspaceAdmin.permissionGroups.personPickerPlaceholder')} />
                      </SelectTrigger>
                      <SelectContent className="z-[100] bg-popover">
                        {(personQuery.data ?? []).map((person) => (
                          <SelectItem key={person.person_id} value={person.person_id}>
                            {person.person_name} · {person.platform} · {person.account_id} · {person.person_id}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    disabled={!memberPersonId || memberMutation.isPending}
                    onClick={() => memberMutation.mutate()}
                  >
                    <Plus className="mr-1 size-4" />
                    {t('workspaceAdmin.common.add')}
                  </Button>
                </div>
                <div className="space-y-2">
                  {detail.members.map((member) => (
                    <div
                      key={member.person_id}
                      className="flex items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium">{member.person_name}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {member.platform} · {member.account_id} · {member.person_id}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('workspaceAdmin.common.delete')}
                        onClick={() => removeMemberMutation.mutate(member.person_id)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                  {!detail.members.length && (
                    <div className="py-4 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.common.noRecords')}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.permissionGroups.contexts')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.permissionGroups.contextsHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid gap-2 md:grid-cols-4">
                  <Select
                    value={contextForm.scope_type}
                    onValueChange={(value: 'global' | 'workspace' | 'session' | 'channel') =>
                      setContextForm({ ...contextForm, scope_type: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {(catalog?.scope_types ?? ['global', 'workspace', 'session', 'channel']).map((scope) => (
                        <SelectItem key={scope} value={scope}>
                          {scope}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {contextForm.scope_type === 'workspace' && (
                    <Input
                      placeholder="workspace_id"
                      value={contextForm.workspace_id}
                      onChange={(event) => setContextForm({ ...contextForm, workspace_id: event.target.value })}
                    />
                  )}
                  {contextForm.scope_type === 'session' && (
                    <Input
                      placeholder="session_id"
                      value={contextForm.session_id}
                      onChange={(event) => setContextForm({ ...contextForm, session_id: event.target.value })}
                    />
                  )}
                  {contextForm.scope_type === 'channel' && (
                    <Select
                      value={contextForm.channel_type}
                      onValueChange={(value: 'private' | 'group') =>
                        setContextForm({ ...contextForm, channel_type: value })
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="z-[100] bg-popover">
                        <SelectItem value="private">private</SelectItem>
                        <SelectItem value="group">group</SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                  <div className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={contextForm.allow_group_disclosure}
                      onCheckedChange={(checked) =>
                        setContextForm({ ...contextForm, allow_group_disclosure: checked })
                      }
                    />
                    {t('workspaceAdmin.permissionGroups.groupDisclosure')}
                  </div>
                  <Button onClick={() => contextMutation.mutate()} disabled={contextMutation.isPending}>
                    <Plus className="mr-1 size-4" />
                    {t('workspaceAdmin.common.add')}
                  </Button>
                </div>
                <div className="space-y-2">
                  {detail.contexts.map((context) => (
                    <div
                      key={context.id}
                      className="flex items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline">{context.scope_type}</Badge>
                        {context.workspace_id && <span className="font-mono text-xs">{context.workspace_id}</span>}
                        {context.session_id && <span className="font-mono text-xs">{context.session_id}</span>}
                        {context.channel_type && <Badge variant="secondary">{context.channel_type}</Badge>}
                        {context.allow_group_disclosure && (
                          <Badge variant="destructive">
                            {t('workspaceAdmin.permissionGroups.groupDisclosure')}
                          </Badge>
                        )}
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('workspaceAdmin.common.delete')}
                        onClick={() => removeContextMutation.mutate(context.id)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                  {!detail.contexts.length && (
                    <div className="py-4 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.permissionGroups.noContextWarning')}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.permissionGroups.capabilities')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.permissionGroups.capabilitiesHint')}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-2 md:grid-cols-2">
                  {(catalog?.capabilities ?? []).map((capability) => (
                    <label
                      key={capability}
                      className="flex cursor-pointer items-center gap-2 rounded-lg border p-2 text-xs"
                    >
                      <Checkbox
                        checked={enabledCapabilities.has(capability)}
                        onCheckedChange={(checked) => {
                          if (checked) {
                            capabilityMutation.mutate({ capability, enabled: true })
                          } else {
                            removeCapabilityMutation.mutate(capability)
                          }
                        }}
                      />
                      <span className="font-mono break-all">{capability}</span>
                    </label>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.permissionGroups.rules')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.permissionGroups.rulesHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid gap-2 md:grid-cols-5">
                  <Select
                    value={ruleForm.effect}
                    onValueChange={(value: 'allow' | 'deny') => setRuleForm({ ...ruleForm, effect: value })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      <SelectItem value="allow">{t('workspaceAdmin.common.allow')}</SelectItem>
                      <SelectItem value="deny">{t('workspaceAdmin.common.deny')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select
                    value={ruleForm.space_selector}
                    onValueChange={(value: RuleCreateInput['space_selector']) =>
                      setRuleForm({ ...ruleForm, space_selector: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {(catalog?.space_selectors ?? []).map((selector) => (
                        <SelectItem key={selector} value={selector}>
                          {selector}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={ruleForm.partition_type}
                    onValueChange={(value: RuleCreateInput['partition_type']) =>
                      setRuleForm({ ...ruleForm, partition_type: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {(catalog?.partition_types ?? []).map((item) => (
                        <SelectItem key={item} value={item}>
                          {item}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={ruleForm.partition_selector}
                    onValueChange={(value: RuleCreateInput['partition_selector']) =>
                      setRuleForm({ ...ruleForm, partition_selector: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {(catalog?.partition_selectors ?? []).map((item) => (
                        <SelectItem key={item} value={item}>
                          {item}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button onClick={() => ruleMutation.mutate()} disabled={ruleMutation.isPending}>
                    <Plus className="mr-1 size-4" />
                    {t('workspaceAdmin.common.add')}
                  </Button>
                </div>
                <div className="space-y-2">
                  {detail.rules.map((rule) => (
                    <div
                      key={rule.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-xs"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={rule.effect === 'allow' ? 'secondary' : 'destructive'}>{rule.effect}</Badge>
                        <Badge variant="outline">{rule.space_selector}</Badge>
                        <Badge variant="outline">{rule.partition_type}</Badge>
                        <Badge variant="outline">{rule.partition_selector}</Badge>
                        {rule.memory_space_id && <span className="font-mono">{rule.memory_space_id}</span>}
                        {rule.partition_key && <span className="font-mono">{rule.partition_key}</span>}
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('workspaceAdmin.common.delete')}
                        onClick={() => deleteRuleMutation.mutate(rule.id)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                  {!detail.rules.length && (
                    <div className="py-4 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.common.noRecords')}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.permissionGroups.botRules')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.permissionGroups.botRulesHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid gap-2 md:grid-cols-4">
                  <Select
                    value={botRuleForm.effect}
                    onValueChange={(value: 'allow' | 'deny') => setBotRuleForm({ ...botRuleForm, effect: value })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      <SelectItem value="allow">{t('workspaceAdmin.common.allow')}</SelectItem>
                      <SelectItem value="deny">{t('workspaceAdmin.common.deny')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select
                    value={botRuleForm.bot_selector}
                    onValueChange={(value: 'public' | 'current_group' | 'kami' | 'specific') =>
                      setBotRuleForm({ ...botRuleForm, bot_selector: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {(catalog?.bot_selectors ?? []).map((selector) => (
                        <SelectItem key={selector} value={selector}>
                          {selector}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {botRuleForm.bot_selector === 'specific' && (
                    <Input
                      placeholder="bot_profile_id"
                      value={botRuleForm.bot_profile_id}
                      onChange={(event) => setBotRuleForm({ ...botRuleForm, bot_profile_id: event.target.value })}
                    />
                  )}
                  <Button onClick={() => botRuleMutation.mutate()} disabled={botRuleMutation.isPending}>
                    <Plus className="mr-1 size-4" />
                    {t('workspaceAdmin.common.add')}
                  </Button>
                </div>
                <div className="space-y-2">
                  {detail.bot_rules.map((rule) => (
                    <div
                      key={rule.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-xs"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={rule.effect === 'allow' ? 'secondary' : 'destructive'}>{rule.effect}</Badge>
                        <Badge variant="outline">{rule.bot_selector}</Badge>
                        {rule.bot_profile_id && <span className="font-mono">{rule.bot_profile_id}</span>}
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('workspaceAdmin.common.delete')}
                        onClick={() => removeBotRuleMutation.mutate(rule.id)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                  {!detail.bot_rules.length && (
                    <div className="py-4 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.common.noRecords')}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
