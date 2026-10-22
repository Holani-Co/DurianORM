import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "CRM WhatsApp Automation" settings section: rules that send a
// WhatsApp template when a Zoho CRM deal reaches a stage (served by the
// zoho-bridge through the admin Rails proxy).
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/crm-automation'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'crm_automation_index',
          component: Index,
          meta: { permissions: ['administrator'] },
        },
      ],
    },
  ],
};
