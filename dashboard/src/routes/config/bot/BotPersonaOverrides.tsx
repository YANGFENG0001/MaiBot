import { useCallback, useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Pencil, Plus, RefreshCw, Route, Trash2, UserRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import {
  createBotProfile,
  deleteBotProfile,
  getBotProfiles,
  getBotRoutes,
  resetBotRoute,
  setBotRoute,
  updateBotProfile,
} from '@/lib/bot-profiles-api'
import type { BotProfileItem } from '@/lib/bot-profiles-api'
import { createPersona, deletePersona, getPersonas, personaInUse, updatePersona } from '@/lib/personas-api'
import type { PersonaItem, PersonaTextField } from '@/lib/personas-api'
import { getWorkspaces } from '@/lib/workspaces-api'

/** Radix Select 不接受空字符串作为 value，用哨兵值表示「不绑定人设」。 */
const INHERIT_PERSONA = '__inherit__'

type PersonaDraft = Record<PersonaTextField, string> & {
  name: string
  /** 界面上别名是一个文本框，保存时由后端按中英文逗号/顿号切分。 */
  alias_names: string
}

const PERSONA_FIELD_KEYS: PersonaTextField[] = [
  'nickname',
  'personality',
  'behavior_style',
  'reply_style',
  'group_chat_prompt',
  'private_chat_prompt',
  'multiple_reply_style',
  'emotion_trait',
  'description',
]

/** 多行输入更适合提示词类字段。 */
const MULTILINE_FIELDS = new Set<PersonaTextField>([
  'personality',
  'behavior_style',
  'group_chat_prompt',
  'private_chat_prompt',
  'description',
])

function personaToDraft(persona: PersonaItem): PersonaDraft {
  return {
    name: persona.name,
    alias_names: persona.alias_names.join(', '),
    description: persona.description,
    nickname: persona.nickname,
    personality: persona.personality,
    behavior_style: persona.behavior_style,
    reply_style: persona.reply_style,
    group_chat_prompt: persona.group_chat_prompt,
    private_chat_prompt: persona.private_chat_prompt,
    multiple_reply_style: persona.multiple_reply_style,
    emotion_trait: persona.emotion_trait,
  }
}

function profileBadgeVariant(profileType: string) {
  if (profileType === 'kami') return 'destructive' as const
  if (profileType === 'group') return 'default' as const
  return 'secondary' as const
}

/**
 * 「按 Bot 覆盖」人设配置面板。
 *
 * 数据模型里人设（persona_profiles）与 BotProfile 的绑定早就存在，运行期
 * （`maisaka_generator_base` / `chat_loop_service`）也会读 `PersonaOverlay`，
 * 但此前没有任何写入入口——人设只能靠数据库迁移写入。这个面板补齐了写入侧：
 * 选 Bot → 绑人设 → 改字段，留空的字段继续继承全局 `bot.personality`。
 */
export function BotPersonaOverrides() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const profilesQuery = useQuery({ queryKey: ['bot-profiles'], queryFn: getBotProfiles })
  const personasQuery = useQuery({ queryKey: ['personas'], queryFn: getPersonas })
  const routesQuery = useQuery({ queryKey: ['bot-routes'], queryFn: getBotRoutes })
  const workspacesQuery = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces })

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data])
  const personas = useMemo(() => personasQuery.data ?? [], [personasQuery.data])
  const routes = useMemo(() => routesQuery.data ?? [], [routesQuery.data])
  const memorySpaces = useMemo(() => workspacesQuery.data?.memory_spaces ?? [], [workspacesQuery.data])

  const [selectedProfileId, setSelectedProfileId] = useState('')
  // 草稿用「覆盖值 + 来源键」表示，而不是把服务端数据同步进 state。
  // 这样换 Bot / 人设在别处被改过时草稿会自动失效回到最新值，不需要 useEffect
  // 去同步（那会触发 react-hooks/set-state-in-effect 警告与级联渲染）。
  const [draftOverride, setDraftOverride] = useState<{ key: string; value: PersonaDraft } | null>(null)
  const [createBotOpen, setCreateBotOpen] = useState(false)
  const [createBotForm, setCreateBotForm] = useState({ name: '', home_memory_space_id: '' })
  const [createPersonaOpen, setCreatePersonaOpen] = useState(false)
  const [createPersonaForm, setCreatePersonaForm] = useState({ name: '', nickname: '', personality: '' })
  const [routeForm, setRouteForm] = useState({ sessionId: '', profileId: '' })

  const selected: BotProfileItem | undefined = useMemo(
    () => profiles.find((item) => item.id === selectedProfileId) ?? profiles[0],
    [profiles, selectedProfileId],
  )

  const boundPersona: PersonaItem | undefined = useMemo(
    () => personas.find((item) => item.id === selected?.persona_profile_id),
    [personas, selected],
  )

  // 人设换了、或它在别处被改过（updated_at 变化）时，草稿键随之变化，
  // 用户正在编辑的内容会被自动丢弃并回到服务端最新值。
  const draftKey = boundPersona ? `${boundPersona.id}:${boundPersona.updated_at}` : ''
  const draft = useMemo<PersonaDraft | null>(() => {
    if (!boundPersona) return null
    return draftOverride?.key === draftKey ? draftOverride.value : personaToDraft(boundPersona)
  }, [boundPersona, draftKey, draftOverride])

  const setDraft = useCallback(
    (value: PersonaDraft) => setDraftOverride({ key: draftKey, value }),
    [draftKey],
  )

  const refreshAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['bot-profiles'] }),
      queryClient.invalidateQueries({ queryKey: ['personas'] }),
      queryClient.invalidateQueries({ queryKey: ['bot-routes'] }),
    ])
  }

  const onError = (error: unknown) => {
    toast({
      title: t('botPersonaOverrides.operationFailed'),
      description: error instanceof Error ? error.message : String(error),
      variant: 'destructive',
    })
  }

  const bindMutation = useMutation({
    mutationFn: ({ profileId, personaId, revision }: { profileId: string; personaId: string | null; revision: number }) =>
      updateBotProfile(profileId, { persona_profile_id: personaId, expected_revision: revision }),
    onSuccess: async () => {
      await refreshAll()
      toast({ title: t('botPersonaOverrides.bindSaved') })
    },
    onError,
  })

  const savePersonaMutation = useMutation({
    mutationFn: ({ personaId, value }: { personaId: string; value: PersonaDraft }) =>
      updatePersona(personaId, {
        name: value.name,
        alias_names: value.alias_names,
        ...Object.fromEntries(PERSONA_FIELD_KEYS.map((key) => [key, value[key]])),
      }),
    onSuccess: async () => {
      await refreshAll()
      toast({ title: t('botPersonaOverrides.personaSaved') })
    },
    onError,
  })

  const createPersonaMutation = useMutation({
    mutationFn: (value: { name: string; nickname: string; personality: string }) => createPersona(value),
    onSuccess: async (created) => {
      await refreshAll()
      setCreatePersonaOpen(false)
      setCreatePersonaForm({ name: '', nickname: '', personality: '' })
      // 新建后直接绑到当前选中的 Bot 上，省掉一次手动选择。
      if (selected) {
        bindMutation.mutate({ profileId: selected.id, personaId: created.id, revision: selected.policy_revision })
      }
    },
    onError,
  })

  const deletePersonaMutation = useMutation({
    mutationFn: (personaId: string) => deletePersona(personaId),
    onSuccess: async () => {
      await refreshAll()
      toast({ title: t('botPersonaOverrides.personaDeleted') })
    },
    onError,
  })

  const createBotMutation = useMutation({
    mutationFn: (value: { name: string; home_memory_space_id: string }) => createBotProfile(value),
    onSuccess: async (created) => {
      await refreshAll()
      setSelectedProfileId(created.id)
      setCreateBotOpen(false)
      setCreateBotForm({ name: '', home_memory_space_id: '' })
      toast({ title: t('botPersonaOverrides.botCreated') })
    },
    onError,
  })

  const deleteBotMutation = useMutation({
    mutationFn: (profileId: string) => deleteBotProfile(profileId),
    onSuccess: async () => {
      await refreshAll()
      setSelectedProfileId('')
      toast({ title: t('botPersonaOverrides.botDeleted') })
    },
    onError,
  })

  const setRouteMutation = useMutation({
    mutationFn: ({ sessionId, profileId }: { sessionId: string; profileId: string }) =>
      setBotRoute(sessionId, profileId),
    onSuccess: async () => {
      await refreshAll()
      setRouteForm({ sessionId: '', profileId: '' })
      toast({ title: t('botPersonaOverrides.routeSaved') })
    },
    onError,
  })

  const resetRouteMutation = useMutation({
    mutationFn: (sessionId: string) => resetBotRoute(sessionId),
    onSuccess: async () => {
      await refreshAll()
      toast({ title: t('botPersonaOverrides.routeReset') })
    },
    onError,
  })

  const loading = profilesQuery.isLoading || personasQuery.isLoading
  const draftDirty = useMemo(() => {
    if (!draft || !boundPersona) return false
    return JSON.stringify(draft) !== JSON.stringify(personaToDraft(boundPersona))
  }, [draft, boundPersona])

  if (loading) {
    return <div className="p-6 text-sm text-muted-foreground">{t('workspaceAdmin.common.loading')}</div>
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">{t('botPersonaOverrides.title')}</h2>
          <p className="text-sm text-muted-foreground">{t('botPersonaOverrides.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void refreshAll()}>
            <RefreshCw className="mr-2 size-4" />
            {t('workspaceAdmin.common.refresh')}
          </Button>
          <Dialog open={createBotOpen} onOpenChange={setCreateBotOpen}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="mr-2 size-4" />
                {t('botPersonaOverrides.createBot')}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t('botPersonaOverrides.createBot')}</DialogTitle>
                <DialogDescription>{t('botPersonaOverrides.createBotHint')}</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="new-bot-name">{t('botPersonaOverrides.botName')}</Label>
                  <Input
                    id="new-bot-name"
                    value={createBotForm.name}
                    onChange={(event) => setCreateBotForm({ ...createBotForm, name: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t('botPersonaOverrides.homeSpace')}</Label>
                  <Select
                    value={createBotForm.home_memory_space_id}
                    onValueChange={(value) => setCreateBotForm({ ...createBotForm, home_memory_space_id: value })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('botPersonaOverrides.selectSpace')} />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {memorySpaces.map((space) => (
                        <SelectItem key={space.id} value={space.id}>
                          {space.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setCreateBotOpen(false)}>
                  {t('workspaceAdmin.common.cancel')}
                </Button>
                <Button
                  disabled={
                    !createBotForm.name.trim() ||
                    !createBotForm.home_memory_space_id ||
                    createBotMutation.isPending
                  }
                  onClick={() => createBotMutation.mutate(createBotForm)}
                >
                  {t('workspaceAdmin.common.create')}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {profiles.length === 0 ? (
        <Alert>
          <AlertTitle>{t('botPersonaOverrides.noBots')}</AlertTitle>
          <AlertDescription>{t('botPersonaOverrides.noBotsHint')}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card className="h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Bot className="size-4" />
              {t('botPersonaOverrides.botList')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {profiles.map((profile) => (
              <button
                key={profile.id}
                type="button"
                onClick={() => setSelectedProfileId(profile.id)}
                aria-pressed={selected?.id === profile.id}
                className={`flex w-full items-center gap-2 rounded-md border px-2 py-2 text-left text-sm transition-colors ${
                  selected?.id === profile.id ? 'border-primary bg-accent' : 'border-transparent hover:bg-accent/50'
                }`}
              >
                <Badge variant={profileBadgeVariant(profile.profile_type)}>{profile.profile_type}</Badge>
                <span className="min-w-0 flex-1 truncate font-medium">{profile.name}</span>
                {profile.persona_profile_id ? <UserRound className="size-3.5 text-muted-foreground" /> : null}
              </button>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-4">
          {selected ? (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm">{t('botPersonaOverrides.botDetail')}</CardTitle>
                  <CardDescription>
                    {t('workspaceAdmin.botProfiles.revision', { revision: selected.policy_revision })}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <div className="text-xs text-muted-foreground">{t('botPersonaOverrides.botName')}</div>
                      <div className="text-sm font-medium">{selected.name}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">{t('botPersonaOverrides.homeSpace')}</div>
                      <div className="text-sm">{selected.home_memory_space_name}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">{t('workspaceAdmin.botProfiles.lineage')}</div>
                      <div className="text-sm">
                        {selected.lineage.map((item) => item.name).join(' → ') || t('workspaceAdmin.common.none')}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">{t('botPersonaOverrides.parentBot')}</div>
                      <div className="text-sm">
                        {profiles.find((item) => item.id === selected.parent_profile_id)?.name ??
                          t('workspaceAdmin.common.none')}
                      </div>
                    </div>
                  </div>

                  <Separator />

                  <div className="space-y-2">
                    <Label>{t('workspaceAdmin.botProfiles.persona')}</Label>
                    <div className="flex flex-wrap items-center gap-2">
                      <Select
                        value={selected.persona_profile_id ?? INHERIT_PERSONA}
                        onValueChange={(value) =>
                          bindMutation.mutate({
                            profileId: selected.id,
                            personaId: value === INHERIT_PERSONA ? null : value,
                            revision: selected.policy_revision,
                          })
                        }
                      >
                        <SelectTrigger className="w-[260px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent className="z-[100] bg-popover">
                          <SelectItem value={INHERIT_PERSONA}>
                            {t('workspaceAdmin.botProfiles.inheritGlobalPersona')}
                          </SelectItem>
                          {personas.map((persona) => (
                            <SelectItem key={persona.id} value={persona.id}>
                              {persona.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Dialog open={createPersonaOpen} onOpenChange={setCreatePersonaOpen}>
                        <DialogTrigger asChild>
                          <Button variant="outline" size="sm">
                            <Plus className="mr-2 size-4" />
                            {t('botPersonaOverrides.createPersona')}
                          </Button>
                        </DialogTrigger>
                        <DialogContent>
                          <DialogHeader>
                            <DialogTitle>{t('botPersonaOverrides.createPersona')}</DialogTitle>
                            <DialogDescription>{t('botPersonaOverrides.createPersonaHint')}</DialogDescription>
                          </DialogHeader>
                          <div className="space-y-4">
                            <div className="space-y-2">
                              <Label htmlFor="new-persona-name">{t('botPersonaOverrides.personaName')}</Label>
                              <Input
                                id="new-persona-name"
                                value={createPersonaForm.name}
                                onChange={(event) =>
                                  setCreatePersonaForm({ ...createPersonaForm, name: event.target.value })
                                }
                              />
                            </div>
                            <div className="space-y-2">
                              <Label htmlFor="new-persona-nickname">
                                {t('botPersonaOverrides.field.nickname')}
                              </Label>
                              <Input
                                id="new-persona-nickname"
                                value={createPersonaForm.nickname}
                                onChange={(event) =>
                                  setCreatePersonaForm({ ...createPersonaForm, nickname: event.target.value })
                                }
                              />
                            </div>
                            <div className="space-y-2">
                              <Label htmlFor="new-persona-personality">
                                {t('botPersonaOverrides.field.personality')}
                              </Label>
                              <Textarea
                                id="new-persona-personality"
                                value={createPersonaForm.personality}
                                onChange={(event) =>
                                  setCreatePersonaForm({ ...createPersonaForm, personality: event.target.value })
                                }
                              />
                            </div>
                          </div>
                          <DialogFooter>
                            <Button variant="outline" onClick={() => setCreatePersonaOpen(false)}>
                              {t('workspaceAdmin.common.cancel')}
                            </Button>
                            <Button
                              disabled={!createPersonaForm.name.trim() || createPersonaMutation.isPending}
                              onClick={() => createPersonaMutation.mutate(createPersonaForm)}
                            >
                              {t('workspaceAdmin.common.create')}
                            </Button>
                          </DialogFooter>
                        </DialogContent>
                      </Dialog>

                      {boundPersona ? (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={personaInUse(boundPersona) || deletePersonaMutation.isPending}
                          title={
                            personaInUse(boundPersona)
                              ? t('botPersonaOverrides.personaInUse')
                              : t('workspaceAdmin.common.delete')
                          }
                          onClick={() => deletePersonaMutation.mutate(boundPersona.id)}
                        >
                          <Trash2 className="mr-2 size-4" />
                          {t('workspaceAdmin.common.delete')}
                        </Button>
                      ) : null}

                      {selected.is_system ? null : (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={deleteBotMutation.isPending}
                          onClick={() => deleteBotMutation.mutate(selected.id)}
                        >
                          <Trash2 className="mr-2 size-4" />
                          {t('botPersonaOverrides.deleteBot')}
                        </Button>
                      )}
                    </div>
                    {!selected.persona_profile_id ? (
                      <p className="text-xs text-muted-foreground">
                        {t('botPersonaOverrides.inheritHint', { name: selected.name })}
                      </p>
                    ) : null}
                  </div>
                </CardContent>
              </Card>

              {boundPersona && draft ? (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <Pencil className="size-4" />
                      {t('botPersonaOverrides.editPersona')}
                    </CardTitle>
                    <CardDescription>{t('botPersonaOverrides.fieldHint')}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="persona-name">{t('botPersonaOverrides.personaName')}</Label>
                        <Input
                          id="persona-name"
                          value={draft.name}
                          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="persona-alias">{t('botPersonaOverrides.field.alias_names')}</Label>
                        <Input
                          id="persona-alias"
                          value={draft.alias_names}
                          onChange={(event) => setDraft({ ...draft, alias_names: event.target.value })}
                        />
                      </div>
                    </div>

                    {PERSONA_FIELD_KEYS.map((key) => {
                      const overridden = draft[key].trim().length > 0
                      return (
                        <div key={key} className="space-y-2">
                          <div className="flex items-center gap-2">
                            <Label htmlFor={`persona-${key}`}>{t(`botPersonaOverrides.field.${key}`)}</Label>
                            <Badge variant={overridden ? 'default' : 'secondary'}>
                              {overridden
                                ? t('botPersonaOverrides.overridden')
                                : t('botPersonaOverrides.inherited')}
                            </Badge>
                          </div>
                          {MULTILINE_FIELDS.has(key) ? (
                            <Textarea
                              id={`persona-${key}`}
                              value={draft[key]}
                              onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
                            />
                          ) : (
                            <Input
                              id={`persona-${key}`}
                              value={draft[key]}
                              onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
                            />
                          )}
                        </div>
                      )
                    })}

                    <div className="flex items-center gap-2">
                      <Button
                        disabled={!draftDirty || !draft.name.trim() || savePersonaMutation.isPending}
                        onClick={() =>
                          savePersonaMutation.mutate({ personaId: boundPersona.id, value: draft })
                        }
                      >
                        {t('botPersonaOverrides.savePersona')}
                      </Button>
                      <Button
                        variant="outline"
                        disabled={!draftDirty}
                        onClick={() => setDraft(personaToDraft(boundPersona))}
                      >
                        {t('botPersonaOverrides.resetPersona')}
                      </Button>
                      {draftDirty ? (
                        <span className="text-xs text-muted-foreground">
                          {t('botPersonaOverrides.unsaved')}
                        </span>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>
              ) : null}
            </>
          ) : null}

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Route className="size-4" />
                {t('botPersonaOverrides.routeTitle')}
              </CardTitle>
              <CardDescription>{t('botPersonaOverrides.routeHint')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-end gap-2">
                <div className="min-w-[220px] flex-1 space-y-2">
                  <Label htmlFor="route-session">{t('botPersonaOverrides.sessionId')}</Label>
                  <Input
                    id="route-session"
                    value={routeForm.sessionId}
                    placeholder="session-id"
                    onChange={(event) => setRouteForm({ ...routeForm, sessionId: event.target.value })}
                  />
                </div>
                <div className="min-w-[180px] space-y-2">
                  <Label>{t('botPersonaOverrides.useBot')}</Label>
                  <Select
                    value={routeForm.profileId}
                    onValueChange={(value) => setRouteForm({ ...routeForm, profileId: value })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('botPersonaOverrides.selectBot')} />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      {profiles
                        .filter((profile) => profile.profile_type !== 'kami')
                        .map((profile) => (
                          <SelectItem key={profile.id} value={profile.id}>
                            {profile.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  disabled={!routeForm.sessionId.trim() || !routeForm.profileId || setRouteMutation.isPending}
                  onClick={() =>
                    setRouteMutation.mutate({ sessionId: routeForm.sessionId.trim(), profileId: routeForm.profileId })
                  }
                >
                  {t('botPersonaOverrides.addRoute')}
                </Button>
              </div>

              {routes.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('botPersonaOverrides.noRoutes')}</p>
              ) : (
                <div className="space-y-1">
                  {routes.map((route) => (
                    <div
                      key={route.session_id}
                      className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-xs">{route.session_id}</span>
                      <Badge variant="outline">{route.active_bot_profile_name}</Badge>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={t('botPersonaOverrides.resetRoute')}
                        onClick={() => resetRouteMutation.mutate(route.session_id)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

export default BotPersonaOverrides
