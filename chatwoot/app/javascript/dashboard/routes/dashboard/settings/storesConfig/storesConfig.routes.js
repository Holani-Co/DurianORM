import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Stores" settings section. Reads the live store/showroom registry
// from the zoho-bridge (via the admin Rails proxy) so administrators can edit
// the store details — address, manager, phone, map, etc. — that are sent to
// customers, without a developer or a redeploy.
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/stores-config'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'stores_config_index',
          component: Index,
          meta: {
            permissions: ['administrator'],
          },
        },
      ],
    },
  ],
};
