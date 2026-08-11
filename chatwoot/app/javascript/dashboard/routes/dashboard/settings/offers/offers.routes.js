import { frontendURL } from 'dashboard/helper/URLHelper';
import SettingsWrapper from '../SettingsWrapper.vue';

const Index = () => import('./Index.vue');

// Admin-only "Offers" settings section. The client manages promotional offers
// (image + caption + priority + tags) that the bot surfaces on greetings.
export default {
  routes: [
    {
      path: frontendURL('accounts/:accountId/settings/offers'),
      component: SettingsWrapper,
      children: [
        {
          path: '',
          name: 'offers_index',
          component: Index,
          meta: {
            permissions: ['administrator'],
          },
        },
      ],
    },
  ],
};
