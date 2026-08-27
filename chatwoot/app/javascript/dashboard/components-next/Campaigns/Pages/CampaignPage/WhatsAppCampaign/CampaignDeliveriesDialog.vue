<script setup>
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useStore } from 'dashboard/composables/store';

import Dialog from 'dashboard/components-next/dialog/Dialog.vue';
import Spinner from 'dashboard/components-next/spinner/Spinner.vue';
import Button from 'dashboard/components-next/button/Button.vue';

const { t } = useI18n();
const store = useStore();
const dialogRef = ref(null);
const deliveries = ref([]);
const campaign = ref(null);
const isLoading = ref(false);

const statusClass = status => {
  if (['delivered', 'read'].includes(status))
    return 'bg-n-teal-3 text-n-teal-11';
  if (status === 'failed') return 'bg-n-ruby-3 text-n-ruby-11';
  if (status === 'skipped') return 'bg-n-slate-3 text-n-slate-11';
  return 'bg-n-amber-3 text-n-amber-11';
};

const open = async selectedCampaign => {
  campaign.value = selectedCampaign;
  deliveries.value = [];
  dialogRef.value.open();
  isLoading.value = true;
  try {
    deliveries.value = await store.dispatch(
      'campaigns/getDeliveries',
      selectedCampaign.id
    );
  } finally {
    isLoading.value = false;
  }
};

defineExpose({ open });
</script>

<template>
  <Dialog
    ref="dialogRef"
    width="2xl"
    :title="t('CAMPAIGN.WHATSAPP.DELIVERIES.TITLE', { name: campaign?.title })"
    :show-cancel-button="false"
    :show-confirm-button="false"
    overflow-y-auto
  >
    <div v-if="isLoading" class="flex justify-center py-10 text-n-slate-11">
      <Spinner />
    </div>
    <div
      v-else
      class="max-h-[60vh] overflow-y-auto rounded-lg border border-n-weak"
    >
      <div
        v-for="delivery in deliveries"
        :key="delivery.id"
        class="flex items-center gap-3 border-b border-n-weak px-3 py-2.5 last:border-b-0"
      >
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium text-n-slate-12">
            {{ delivery.contact.name || delivery.phone_number }}
          </p>
          <p class="truncate text-xs text-n-slate-10">
            {{ delivery.phone_number || delivery.skip_reason }}
          </p>
          <p
            v-if="delivery.error_message"
            class="truncate text-xs text-n-ruby-10"
          >
            {{ delivery.error_message }}
          </p>
        </div>
        <span
          class="rounded-md px-2 py-0.5 text-xs font-medium"
          :class="statusClass(delivery.status)"
        >
          {{ delivery.status }}
        </span>
      </div>
      <p
        v-if="deliveries.length === 0"
        class="px-4 py-10 text-center text-sm text-n-slate-11"
      >
        {{ t('CAMPAIGN.WHATSAPP.DELIVERIES.EMPTY') }}
      </p>
    </div>
    <template #footer>
      <div class="flex justify-end">
        <Button
          color="slate"
          variant="faded"
          :label="t('CAMPAIGN.WHATSAPP.DELIVERIES.CLOSE')"
          @click="dialogRef.close()"
        />
      </div>
    </template>
  </Dialog>
</template>
