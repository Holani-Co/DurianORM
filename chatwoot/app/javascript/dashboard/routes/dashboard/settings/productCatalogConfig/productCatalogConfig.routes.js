import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Product Catalog" settings section. Shows the price list the
// social agent uses (from the zoho-bridge, via the admin Rails proxy) and lets
// administrators replace it from a Google Sheet or a file, with rollback.
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/product-catalog'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'product_catalog_config_index',
          component: Index,
          meta: {
            permissions: ['administrator'],
          },
        },
      ],
    },
  ],
};
