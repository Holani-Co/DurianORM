<script setup>
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useIntervalFn, useToggle } from '@vueuse/core';
import {
  useStore,
  useStoreGetters,
  useMapGetter,
} from 'dashboard/composables/store';

import Spinner from 'dashboard/components-next/spinner/Spinner.vue';
import CampaignLayout from 'dashboard/components-next/Campaigns/CampaignLayout.vue';
import CampaignList from 'dashboard/components-next/Campaigns/Pages/CampaignPage/CampaignList.vue';
import WhatsAppCampaignDialog from 'dashboard/components-next/Campaigns/Pages/CampaignPage/WhatsAppCampaign/WhatsAppCampaignDialog.vue';
import ConfirmDeleteCampaignDialog from 'dashboard/components-next/Campaigns/Pages/CampaignPage/ConfirmDeleteCampaignDialog.vue';
import WhatsAppCampaignEmptyState from 'dashboard/components-next/Campaigns/EmptyState/WhatsAppCampaignEmptyState.vue';
import WhatsAppTemplateDialog from 'dashboard/components-next/Campaigns/Pages/CampaignPage/WhatsAppTemplate/WhatsAppTemplateDialog.vue';
import WhatsAppTemplateList from 'dashboard/components-next/Campaigns/Pages/CampaignPage/WhatsAppTemplate/WhatsAppTemplateList.vue';
import CampaignDeliveriesDialog from 'dashboard/components-next/Campaigns/Pages/CampaignPage/WhatsAppCampaign/CampaignDeliveriesDialog.vue';
import WhatsAppConsentDialog from 'dashboard/components-next/Campaigns/Pages/CampaignPage/WhatsAppConsent/WhatsAppConsentDialog.vue';
import WhatsAppConsentList from 'dashboard/components-next/Campaigns/Pages/CampaignPage/WhatsAppConsent/WhatsAppConsentList.vue';
import TabBar from 'dashboard/components-next/tabbar/TabBar.vue';

const { t } = useI18n();
const store = useStore();
const getters = useStoreGetters();

const selectedCampaign = ref(null);
const selectedTemplate = ref(null);
const activeTab = ref(0);
const [showWhatsAppCampaignDialog, toggleWhatsAppCampaignDialog] = useToggle();
const [showWhatsAppTemplateDialog, toggleWhatsAppTemplateDialog] = useToggle();
const [showWhatsAppConsentDialog, toggleWhatsAppConsentDialog] = useToggle();

const uiFlags = useMapGetter('campaigns/getUIFlags');
const templateUIFlags = useMapGetter('whatsappTemplates/getUIFlags');
const whatsappTemplates = useMapGetter('whatsappTemplates/getTemplates');
const whatsappConsents = useMapGetter('whatsappConsents/getConsents');
const isFetchingCampaigns = computed(() => uiFlags.value.isFetching);

const confirmDeleteCampaignDialogRef = ref(null);
const campaignDeliveriesDialogRef = ref(null);

const WhatsAppCampaigns = computed(
  () => getters['campaigns/getWhatsAppCampaigns'].value
);

const hasNoWhatsAppCampaigns = computed(
  () => WhatsAppCampaigns.value?.length === 0 && !isFetchingCampaigns.value
);

const tabs = computed(() => [
  {
    key: 'campaigns',
    label: t('CAMPAIGN.WHATSAPP.TABS.CAMPAIGNS'),
    count: WhatsAppCampaigns.value.length,
  },
  {
    key: 'templates',
    label: t('CAMPAIGN.WHATSAPP.TABS.TEMPLATES'),
    count: whatsappTemplates.value.length,
  },
  {
    key: 'consents',
    label: t('CAMPAIGN.WHATSAPP.TABS.CONSENTS'),
    count: whatsappConsents.value.length,
  },
]);

const isTemplatesTab = computed(() => activeTab.value === 1);
const isConsentsTab = computed(() => activeTab.value === 2);
const actionLabel = computed(() => {
  if (isTemplatesTab.value) return t('CAMPAIGN.WHATSAPP.TEMPLATES.NEW');
  if (isConsentsTab.value) return t('CAMPAIGN.WHATSAPP.CONSENTS.NEW');
  return t('CAMPAIGN.WHATSAPP.NEW_CAMPAIGN');
});

const handleActionClick = () => {
  if (isTemplatesTab.value) {
    selectedTemplate.value = null;
    toggleWhatsAppTemplateDialog();
  } else if (isConsentsTab.value) {
    toggleWhatsAppConsentDialog();
  } else {
    toggleWhatsAppCampaignDialog();
  }
};

const handleEditTemplate = template => {
  selectedTemplate.value = template;
  toggleWhatsAppTemplateDialog(true);
};

const closeActions = () => {
  toggleWhatsAppCampaignDialog(false);
  toggleWhatsAppTemplateDialog(false);
  toggleWhatsAppConsentDialog(false);
};

const handleTabChanged = tab => {
  activeTab.value = tabs.value.findIndex(item => item.key === tab.key);
  closeActions();
};

const handleDelete = campaign => {
  selectedCampaign.value = campaign;
  confirmDeleteCampaignDialogRef.value.dialogRef.open();
};

const handleCampaignAction = action => campaign => {
  store.dispatch(`campaigns/${action}`, campaign.id);
};

const handlePause = handleCampaignAction('pause');
const handleResume = handleCampaignAction('resume');
const handleCancel = handleCampaignAction('cancel');
const handleViewDeliveries = campaign => {
  campaignDeliveriesDialogRef.value.open(campaign);
};

onMounted(() => {
  if (!templateUIFlags.value.isFetching) {
    store.dispatch('whatsappTemplates/get');
  }
  store.dispatch('whatsappConsents/get');
});

// Poll in the background so the list refreshes without flipping the isFetching
// flag (which would blank the list to a spinner every 15s).
useIntervalFn(
  () => store.dispatch('campaigns/get', { background: true }),
  15000
);
</script>

<template>
  <CampaignLayout
    :header-title="t('CAMPAIGN.WHATSAPP.HEADER_TITLE')"
    :button-label="actionLabel"
    @click="handleActionClick"
    @close="closeActions"
  >
    <template #action>
      <WhatsAppCampaignDialog
        v-if="showWhatsAppCampaignDialog && !isTemplatesTab"
        @close="toggleWhatsAppCampaignDialog(false)"
      />
      <WhatsAppTemplateDialog
        v-if="showWhatsAppTemplateDialog && isTemplatesTab"
        :template="selectedTemplate"
        @close="toggleWhatsAppTemplateDialog(false)"
      />
      <WhatsAppConsentDialog
        v-if="showWhatsAppConsentDialog && isConsentsTab"
        @close="toggleWhatsAppConsentDialog(false)"
      />
    </template>
    <TabBar
      :tabs="tabs"
      :initial-active-tab="activeTab"
      class="mb-6"
      @tab-changed="handleTabChanged"
    />

    <template v-if="!isTemplatesTab">
      <div
        v-if="isFetchingCampaigns && !WhatsAppCampaigns.length"
        class="flex items-center justify-center py-10 text-n-slate-11"
      >
        <Spinner />
      </div>
      <CampaignList
        v-else-if="!hasNoWhatsAppCampaigns"
        :campaigns="WhatsAppCampaigns"
        @delete="handleDelete"
        @pause="handlePause"
        @resume="handleResume"
        @cancel="handleCancel"
        @view-deliveries="handleViewDeliveries"
      />
      <WhatsAppCampaignEmptyState
        v-else
        :title="t('CAMPAIGN.WHATSAPP.EMPTY_STATE.TITLE')"
        :subtitle="t('CAMPAIGN.WHATSAPP.EMPTY_STATE.SUBTITLE')"
        class="pt-14"
      />
    </template>
    <WhatsAppTemplateList
      v-else-if="isTemplatesTab"
      @edit="handleEditTemplate"
    />
    <WhatsAppConsentList v-else />
    <ConfirmDeleteCampaignDialog
      ref="confirmDeleteCampaignDialogRef"
      :selected-campaign="selectedCampaign"
    />
    <CampaignDeliveriesDialog ref="campaignDeliveriesDialogRef" />
  </CampaignLayout>
</template>
