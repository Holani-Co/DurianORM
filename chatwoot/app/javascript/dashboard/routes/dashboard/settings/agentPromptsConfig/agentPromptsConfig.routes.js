import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Agent Prompts" settings section (per-vertical agent guidance).
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/agent-prompts-config'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'agent_prompts_config_index',
          component: Index,
          meta: {
            permissions: ['administrator'],
          },
        },
      ],
    },
  ],
};
