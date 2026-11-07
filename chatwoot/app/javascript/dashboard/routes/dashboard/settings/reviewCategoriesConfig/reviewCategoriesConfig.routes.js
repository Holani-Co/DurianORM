import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Review Categories" settings section (Google-review reply categories).
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/review-categories'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'review_categories_config_index',
          component: Index,
          meta: {
            permissions: ['administrator'],
          },
        },
      ],
    },
  ],
};
