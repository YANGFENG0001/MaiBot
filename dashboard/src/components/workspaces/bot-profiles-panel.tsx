import { useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  getBotProfiles,
  removeBotProfilePlugin,
  removeBotProfileTool,
  setBotProfilePlugin,
  setBotProfileTool,
  updateBotProfile,
} from '@/lib/bot-profiles-api'

import type { BotProfileItem, BotProfileUpdateInput } from '@/lib/bot-profiles-api'

/** 每个变更都必须带上目标 Profile 与当前策略版本，避免闭包捕获到过期对象。 */
interface ProfileMutationTarget {
  profileId: string
  revision: number
}

function profileBadgeVariant(profileType: string) {
  if (profileType === 'kami') return 'destructive' as const
  if (profileType === 'group') return 'default' as const
  return 'secondary' as const
}

export function BotProfilesPanel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [toolName, setToolName] = useState('')
  const [toolEffect, setToolEffect] = useState<'allow' | 'deny'>('deny')
  const [pluginId, setPluginId] = useState('')
  const [pluginEffect, setPluginEffect] = useState<'allow' | 'deny' | 'inherit'>('inherit')

  const profileQuery = useQuery({ queryKey: ['bot-profiles'], queryFn: getBotProfiles })
  const profiles = useMemo(() => profileQuery.data ?? [], [profileQuery.data])
  const selected: BotProfileItem | undefined = useMemo(
    () => profiles.find((item) => item.id === selectedId) ?? profiles[0],
    [selectedId, profiles],
  )

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['bot-profiles'] })
  }

  const updateMutation = useMutation({
    mutationFn: ({ profileId, revision, input }: ProfileMutationTarget & { input: BotProfileUpdateInput }) =>
      updateBotProfile(profileId, { ...input, expected_revision: revision }),
    onSuccess: refresh,
  })

  const toolMutation = useMutation({
    mutationFn: ({ profileId, revision }: ProfileMutationTarget) =>
      setBotProfileTool(profileId, toolName.trim(), toolEffect, revision),
    onSuccess: async () => {
      setToolName('')
      await refresh()
    },
  })

  // 删除类接口是幂等操作，不接受 expected_revision，因此只携带定位参数。
  const removeToolMutation = useMutation({
    mutationFn: ({ profileId, componentName }: { profileId: string; componentName: string }) =>
      removeBotProfileTool(profileId, componentName),
    onSuccess: refresh,
  })

  const pluginMutation = useMutation({
    mutationFn: ({ profileId, revision }: ProfileMutationTarget) =>
      setBotProfilePlugin(profileId, pluginId.trim(), pluginEffect, revision),
    onSuccess: async () => {
      setPluginId('')
      await refresh()
    },
  })

  const removePluginMutation = useMutation({
    mutationFn: ({ profileId, pluginId: targetPluginId }: { profileId: string; pluginId: string }) =>
      removeBotProfilePlugin(profileId, targetPluginId),
    onSuccess: refresh,
  })

  const error =
    profileQuery.error ||
    updateMutation.error ||
    toolMutation.error ||
    removeToolMutation.error ||
    pluginMutation.error ||
    removePluginMutation.error

  if (profileQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">{t('workspaceAdmin.common.loading')}</div>
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Bot className="size-5" />
            {t('workspaceAdmin.botProfiles.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.botProfiles.subtitle')}</p>
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

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-3">
          {profiles.map((profile) => (
            <button
              key={profile.id}
              type="button"
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                selected?.id === profile.id ? 'border-primary bg-primary/5' : 'bg-card hover:bg-accent/50'
              } ${profile.profile_type === 'kami' ? 'border-destructive/60' : ''}`}
              onClick={() => setSelectedId(profile.id)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="font-semibold">{profile.name}</div>
                <Badge variant={profileBadgeVariant(profile.profile_type)}>{profile.profile_type}</Badge>
              </div>
              <div className="mt-2 text-xs text-muted-foreground">
                {t('workspaceAdmin.botProfiles.revision', { revision: profile.policy_revision })} ·{' '}
                {profile.enabled ? t('workspaceAdmin.common.enabled') : t('workspaceAdmin.common.disabled')}
              </div>
            </button>
          ))}
        </div>

        {selected && (
          <div className="space-y-5">
            {selected.profile_type === 'kami' && (
              <Alert variant="destructive">
                <AlertTitle>{t('workspaceAdmin.botProfiles.kamiWarningTitle')}</AlertTitle>
                <AlertDescription>{t('workspaceAdmin.botProfiles.kamiWarningBody')}</AlertDescription>
              </Alert>
            )}

            <Card>
              <CardHeader>
                <CardTitle>{selected.name}</CardTitle>
                <CardDescription>
                  {t('workspaceAdmin.botProfiles.lineage')}:{' '}
                  {selected.lineage.map((item) => item.name).join(' → ') || t('workspaceAdmin.common.none')}
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="rounded-lg border p-3">
                  <div className="text-xs text-muted-foreground">{t('workspaceAdmin.botProfiles.homeSpace')}</div>
                  <div className="mt-1 font-medium">{selected.home_memory_space_name}</div>
                </div>
                <div className="rounded-lg border p-3">
                  <div className="text-xs text-muted-foreground">{t('workspaceAdmin.botProfiles.persona')}</div>
                  <div className="mt-1 font-medium">
                    {selected.persona_profile_id || t('workspaceAdmin.botProfiles.inheritGlobalPersona')}
                  </div>
                </div>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div className="font-medium">{t('workspaceAdmin.common.enabled')}</div>
                  <Switch
                    checked={selected.enabled}
                    onCheckedChange={(checked) => updateMutation.mutate({ profileId: selected.id, revision: selected.policy_revision, input: { enabled: checked } })}
                  />
                </div>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.botProfiles.inheritParentTools')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.botProfiles.inheritParentHint')}
                    </div>
                  </div>
                  <Switch
                    checked={selected.inherit_parent_tools}
                    disabled={selected.profile_type === 'public'}
                    onCheckedChange={(checked) => updateMutation.mutate({ profileId: selected.id, revision: selected.policy_revision, input: { inherit_parent_tools: checked } })}
                  />
                </div>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.botProfiles.inheritParentPlugins')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.botProfiles.inheritParentHint')}
                    </div>
                  </div>
                  <Switch
                    checked={selected.inherit_parent_plugins}
                    disabled={selected.profile_type === 'public'}
                    onCheckedChange={(checked) => updateMutation.mutate({ profileId: selected.id, revision: selected.policy_revision, input: { inherit_parent_plugins: checked } })}
                  />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.botProfiles.toolPolicies')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.botProfiles.toolPoliciesHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[220px] flex-1 space-y-1">
                    <label className="text-xs text-muted-foreground" htmlFor="bot-tool-name">
                      {t('workspaceAdmin.botProfiles.componentName')}
                    </label>
                    <Input
                      id="bot-tool-name"
                      value={toolName}
                      placeholder="plugin_id.component_name"
                      onChange={(event) => setToolName(event.target.value)}
                    />
                  </div>
                  <Select value={toolEffect} onValueChange={(value: 'allow' | 'deny') => setToolEffect(value)}>
                    <SelectTrigger className="w-[140px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      <SelectItem value="allow">{t('workspaceAdmin.common.allow')}</SelectItem>
                      <SelectItem value="deny">{t('workspaceAdmin.common.deny')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    disabled={!toolName.includes('.') || toolMutation.isPending}
                    onClick={() => toolMutation.mutate({ profileId: selected.id, revision: selected.policy_revision })}
                  >
                    <Plus className="mr-1 size-4" />
                    {t('workspaceAdmin.common.add')}
                  </Button>
                </div>
                <div className="space-y-2">
                  {selected.tool_policies.map((policy) => (
                    <div
                      key={policy.component_name}
                      className="flex items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                    >
                      <span className="font-mono text-xs break-all">{policy.component_name}</span>
                      <div className="flex items-center gap-2">
                        <Badge variant={policy.effect === 'allow' ? 'secondary' : 'destructive'}>
                          {policy.effect === 'allow'
                            ? t('workspaceAdmin.common.allow')
                            : t('workspaceAdmin.common.deny')}
                        </Badge>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('workspaceAdmin.common.delete')}
                          onClick={() => removeToolMutation.mutate({ profileId: selected.id, componentName: policy.component_name })}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                  {!selected.tool_policies.length && (
                    <div className="py-4 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.common.noRecords')}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.botProfiles.pluginPolicies')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.botProfiles.pluginPoliciesHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[220px] flex-1 space-y-1">
                    <label className="text-xs text-muted-foreground" htmlFor="bot-plugin-id">
                      {t('workspaceAdmin.botProfiles.pluginId')}
                    </label>
                    <Input
                      id="bot-plugin-id"
                      value={pluginId}
                      onChange={(event) => setPluginId(event.target.value)}
                    />
                  </div>
                  <Select
                    value={pluginEffect}
                    onValueChange={(value: 'allow' | 'deny' | 'inherit') => setPluginEffect(value)}
                  >
                    <SelectTrigger className="w-[140px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      <SelectItem value="allow">{t('workspaceAdmin.common.allow')}</SelectItem>
                      <SelectItem value="deny">{t('workspaceAdmin.common.deny')}</SelectItem>
                      <SelectItem value="inherit">{t('workspaceAdmin.common.inherit')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    disabled={!pluginId.trim() || pluginMutation.isPending}
                    onClick={() => pluginMutation.mutate({ profileId: selected.id, revision: selected.policy_revision })}
                  >
                    <Plus className="mr-1 size-4" />
                    {t('workspaceAdmin.common.add')}
                  </Button>
                </div>
                <div className="space-y-2">
                  {selected.plugin_policies.map((policy) => (
                    <div
                      key={policy.plugin_id}
                      className="flex items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                    >
                      <span className="font-mono text-xs break-all">{policy.plugin_id}</span>
                      <div className="flex items-center gap-2">
                        <Badge variant={policy.effect === 'deny' ? 'destructive' : 'secondary'}>
                          {policy.effect}
                        </Badge>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('workspaceAdmin.common.delete')}
                          onClick={() => removePluginMutation.mutate({ profileId: selected.id, pluginId: policy.plugin_id })}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                  {!selected.plugin_policies.length && (
                    <div className="py-4 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.common.noRecords')}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.botProfiles.memoryRules')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.botProfiles.memoryRulesHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {selected.memory_rules.map((rule) => (
                  <div
                    key={rule.target_space_id}
                    className="flex items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                  >
                    <span>{rule.target_space_name}</span>
                    <Badge variant={rule.can_read ? 'secondary' : 'destructive'}>
                      {rule.can_read ? t('workspaceAdmin.common.allow') : t('workspaceAdmin.common.deny')}
                    </Badge>
                  </div>
                ))}
                {!selected.memory_rules.length && (
                  <div className="py-4 text-center text-sm text-muted-foreground">
                    {t('workspaceAdmin.common.noRecords')}
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
