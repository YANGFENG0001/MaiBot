import { useTranslation } from 'react-i18next'

import { AuditPanel } from '@/components/workspaces/audit-panel'
import { BotProfilesPanel } from '@/components/workspaces/bot-profiles-panel'
import { ChatGroupsPanel } from '@/components/workspaces/chat-groups-panel'
import { KamiPanel } from '@/components/workspaces/kami-panel'
import { MemorySpacesPanel } from '@/components/workspaces/memory-spaces-panel'
import { PermissionGroupsPanel } from '@/components/workspaces/permission-groups-panel'
import { TransfersPanel } from '@/components/workspaces/transfers-panel'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import { Tabs, TabsContent } from '@/components/ui/tabs'

const TAB_KEYS = [
  'chatGroups',
  'botProfiles',
  'memorySpaces',
  'permissionGroups',
  'kami',
  'transfers',
  'audit',
] as const

export function WorkspacesPage() {
  const { t } = useTranslation()

  return (
    <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-6 p-4 md:p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t('workspaceAdmin.pageTitle')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t('workspaceAdmin.pageSubtitle')}</p>
      </div>

      <Tabs defaultValue="chatGroups" className="w-full">
        <DashboardTabBar variant="scroll">
          {TAB_KEYS.map((key) => (
            <DashboardTabTrigger key={key} value={key}>
              {t(`workspaceAdmin.tabs.${key}`)}
            </DashboardTabTrigger>
          ))}
        </DashboardTabBar>

        <TabsContent value="chatGroups">
          <ChatGroupsPanel />
        </TabsContent>
        <TabsContent value="botProfiles">
          <BotProfilesPanel />
        </TabsContent>
        <TabsContent value="memorySpaces">
          <MemorySpacesPanel />
        </TabsContent>
        <TabsContent value="permissionGroups">
          <PermissionGroupsPanel />
        </TabsContent>
        <TabsContent value="kami">
          <KamiPanel />
        </TabsContent>
        <TabsContent value="transfers">
          <TransfersPanel />
        </TabsContent>
        <TabsContent value="audit">
          <AuditPanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
