import { Tabs } from 'expo-router';

import { colors, type } from '@/theme';

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.gold,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarLabelStyle: { fontSize: type.sm, fontWeight: '600' },
        // Words, not icons, for now: the tab names say exactly what is behind
        // them, and an icon set is one more dependency to justify.
        tabBarIcon: () => null,
        tabBarIconStyle: { display: 'none' },
        sceneStyle: { backgroundColor: colors.bg },
      }}>
      <Tabs.Screen name="index" options={{ title: 'Am I being watched?', tabBarLabel: 'Accounts' }} />
      <Tabs.Screen name="alerts" options={{ title: 'Alerts', tabBarLabel: 'Alerts' }} />
      <Tabs.Screen name="settings" options={{ title: 'Security', tabBarLabel: 'Security' }} />
    </Tabs>
  );
}
