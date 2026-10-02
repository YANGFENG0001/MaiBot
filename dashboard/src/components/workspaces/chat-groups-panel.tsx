import { useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, Database, Plus, RefreshCw, Users } from 'lucide-react'
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
  assignWorkspaceChats,
  createWorkspace,
  getAvailableWorkspaceChats,
  getWorkspaces,
  updateWorkspace,
} from '@/lib/workspaces-api'

import type { WorkspaceCreateInput, WorkspaceItem } from '@/lib/workspaces-api'

const EMPTY_CREATE: WorkspaceCreateInput = {
  name: '',
  description: '',
  memory_mode: 'private',
  inherit_global_tools: true,
  inherit_global_plugins: true,
}

export function ChatGroupsPanel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState<WorkspaceCreateInput>(EMPTY_CREATE)
  const [selectedChats, setSelectedChats] = useState<Set<string>>(new Set())

  const workspaceQuery = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces })
  const chatsQuery = useQuery({ queryKey: ['workspace-chats'], queryFn: getAvailableWorkspaceChats })
  const workspaces = useMemo(() => workspaceQuery.data?.data ?? [], [workspaceQuery.data?.data])
  const selectedWorkspace = useMemo(
    () => workspaces.find((item) => item.id === selectedId) ?? workspaces[0],
    [selectedId, workspaces],
  )

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      queryClient.invalidateQueries({ queryKey: ['workspace-chats'] }),
    ])
  }

  const createMutation = useMutation({
    mutationFn: createWorkspace,
    onSuccess: async (workspace) => {
      setSelectedId(workspace.id)
      setCreateOpen(false)
      setCreateForm(EMPTY_CREATE)
      await refresh()
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<WorkspaceItem> }) =>
      updateWorkspace(id, input),
    onSuccess: refresh,
  })

  const assignMutation = useMutation({
    mutationFn: ({ id, sessionIds }: { id: string; sessionIds: string[] }) =>
      assignWorkspaceChats(id, sessionIds),
    onSuccess: async () => {
      setSelectedChats(new Set())
      await refresh()
    },
  })

  const visibleChats = chatsQuery.data ?? []
  const selectedMembers = selectedWorkspace
    ? visibleChats.filter((chat) => chat.workspace_id === selectedWorkspace.id)
    : []

  const toggleChat = (sessionId: string) => {
    setSelectedChats((current) => {
      const next = new Set(current)
      if (next.has(sessionId)) next.delete(sessionId)
      else next.add(sessionId)
      return next
    })
  }

  const error =
    workspaceQuery.error ||
    chatsQuery.error ||
    createMutation.error ||
    updateMutation.error ||
    assignMutation.error

  if (workspaceQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">{t('workspaceAdmin.common.loading')}</div>
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Boxes className="size-5" />
            {t('workspaceAdmin.chatGroups.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.chatGroups.subtitle')}</p>
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
                {t('workspaceAdmin.chatGroups.create')}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t('workspaceAdmin.chatGroups.create')}</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="workspace-name">{t('workspaceAdmin.common.name')}</Label>
                  <Input
                    id="workspace-name"
                    value={createForm.name}
                    onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="workspace-description">{t('workspaceAdmin.common.description')}</Label>
                  <Textarea
                    id="workspace-description"
                    value={createForm.description}
                    onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t('workspaceAdmin.chatGroups.memoryMode')}</Label>
                  <Select
                    value={createForm.memory_mode}
                    onValueChange={(value: 'private' | 'public') =>
                      setCreateForm({ ...createForm, memory_mode: value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="z-[100] bg-popover">
                      <SelectItem value="private">{t('workspaceAdmin.chatGroups.memoryPrivate')}</SelectItem>
                      <SelectItem value="public">{t('workspaceAdmin.chatGroups.memoryPublic')}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setCreateOpen(false)}>
                  {t('workspaceAdmin.common.cancel')}
                </Button>
                <Button
                  disabled={!createForm.name.trim() || createMutation.isPending}
                  onClick={() => createMutation.mutate(createForm)}
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

      <div className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="space-y-3">
          {workspaces.map((workspace) => (
            <button
              key={workspace.id}
              type="button"
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                selectedWorkspace?.id === workspace.id ? 'border-primary bg-primary/5' : 'bg-card hover:bg-accent/50'
              }`}
              onClick={() => setSelectedId(workspace.id)}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-semibold">{workspace.name}</div>
                  <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {workspace.description || t('workspaceAdmin.common.noDescription')}
                  </div>
                </div>
                {workspace.is_default && <Badge>{t('workspaceAdmin.common.default')}</Badge>}
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <Badge variant="outline">
                  <Users className="mr-1 size-3" />
                  {t('workspaceAdmin.chatGroups.memberCount', { count: workspace.member_count })}
                </Badge>
                <Badge variant="outline">
                  <Database className="mr-1 size-3" />
                  {workspace.memory_space_name}
                </Badge>
              </div>
            </button>
          ))}
        </div>

        {selectedWorkspace && (
          <div className="space-y-5">
            <Card>
              <CardHeader>
                <CardTitle>{selectedWorkspace.name}</CardTitle>
                <CardDescription>
                  {t('workspaceAdmin.chatGroups.revision', { revision: selectedWorkspace.policy_revision })}
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.chatGroups.inheritTools')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.chatGroups.inheritToolsHint')}
                    </div>
                  </div>
                  <Switch
                    checked={selectedWorkspace.inherit_global_tools}
                    onCheckedChange={(checked) =>
                      updateMutation.mutate({ id: selectedWorkspace.id, input: { inherit_global_tools: checked } })
                    }
                  />
                </div>
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <div className="font-medium">{t('workspaceAdmin.chatGroups.inheritPlugins')}</div>
                    <div className="text-xs text-muted-foreground">
                      {t('workspaceAdmin.chatGroups.inheritPluginsHint')}
                    </div>
                  </div>
                  <Switch
                    checked={selectedWorkspace.inherit_global_plugins}
                    onCheckedChange={(checked) =>
                      updateMutation.mutate({ id: selectedWorkspace.id, input: { inherit_global_plugins: checked } })
                    }
                  />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('workspaceAdmin.chatGroups.members')}</CardTitle>
                <CardDescription>{t('workspaceAdmin.chatGroups.membersHint')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="max-h-[430px] space-y-2 overflow-y-auto pr-1">
                  {visibleChats.map((chat) => {
                    const isCurrent = chat.workspace_id === selectedWorkspace.id
                    return (
                      <label
                        key={chat.session_id}
                        className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 hover:bg-accent/40"
                      >
                        <Checkbox
                          checked={selectedChats.has(chat.session_id)}
                          onCheckedChange={() => toggleChat(chat.session_id)}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{chat.display_name}</div>
                          <div className="truncate text-xs text-muted-foreground">
                            {chat.platform} ·{' '}
                            {chat.chat_type === 'group'
                              ? t('workspaceAdmin.chatGroups.group')
                              : t('workspaceAdmin.chatGroups.private')}{' '}
                            · {t('workspaceAdmin.chatGroups.current')}: {chat.workspace_name}
                          </div>
                        </div>
                        {isCurrent && <Badge variant="secondary">{t('workspaceAdmin.chatGroups.currentMember')}</Badge>}
                      </label>
                    )
                  })}
                  {!visibleChats.length && (
                    <div className="py-10 text-center text-sm text-muted-foreground">
                      {t('workspaceAdmin.chatGroups.noChats')}
                    </div>
                  )}
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">
                    {t('workspaceAdmin.chatGroups.selectionSummary', {
                      resolved: selectedMembers.length,
                      selected: selectedChats.size,
                    })}
                  </span>
                  <Button
                    disabled={!selectedChats.size || assignMutation.isPending}
                    onClick={() =>
                      assignMutation.mutate({ id: selectedWorkspace.id, sessionIds: [...selectedChats] })
                    }
                  >
                    {t('workspaceAdmin.chatGroups.saveMembers')}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
