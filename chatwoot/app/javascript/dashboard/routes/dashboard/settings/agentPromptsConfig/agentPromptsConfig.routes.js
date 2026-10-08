import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Agent Prompts" settings section. Reads the live per-vertical
// guidance from the zoho-bridge (via the admin Rails proxy) so administrators
// can tune — per vertical — which store details the social agent leads with and
// how it behaves, without a developer or a redeploy. The guidance is advisory;
// the agent's safety rules always win.
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
