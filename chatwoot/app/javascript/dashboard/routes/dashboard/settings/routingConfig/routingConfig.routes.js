import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Routing Config" settings section. Reads the live email-routing
// rules from the zoho-bridge (via the admin Rails proxy) so administrators can
// manage how inbound email is categorised, forwarded, and assigned to CRM owners.
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/routing-config'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'routing_config_index',
          component: Index,
          meta: {
            permissions: ['administrator'],
          },
        },
      ],
    },
  ],
};
