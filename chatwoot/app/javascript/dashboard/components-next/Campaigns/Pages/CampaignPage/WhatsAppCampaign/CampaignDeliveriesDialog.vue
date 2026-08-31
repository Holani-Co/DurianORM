<script setup>
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useDebounceFn } from '@vueuse/core';
import { useAlert } from 'dashboard/composables';
import { useStore } from 'dashboard/composables/store';
import { downloadCsvFile } from 'dashboard/helper/downloadHelper';

import Dialog from 'dashboard/components-next/dialog/Dialog.vue';
import Spinner from 'dashboard/components-next/spinner/Spinner.vue';
import Button from 'dashboard/components-next/button/Button.vue';
import Input from 'dashboard/components-next/input/Input.vue';
import Select from 'dashboard/components-next/select/Select.vue';

const { t } = useI18n();
const store = useStore();
const dialogRef = ref(null);
const deliveries = ref([]);
const campaign = ref(null);
const isLoading = ref(false);
const isExporting = ref(false);
const statusFilter = ref('');
const searchQuery = ref('');
const currentPage = ref(1);
const totalPages = ref(1);
const totalCount = ref(0);

const statusOptions = computed(() => [
  { value: '', label: t('CAMPAIGN.WHATSAPP.DELIVERIES.ALL_STATUSES') },
  ...[
    'pending',
    'queued',
    'sending',
    'sent',
    'delivered',
    'read',
    'failed',
    'skipped',
    'cancelled',
  ].map(status => ({ value: status, label: status.replace('_', ' ') })),
]);

const statusClass = status => {
  if (['delivered', 'read'].includes(status))
    return 'bg-n-teal-3 text-n-teal-11';
  if (status === 'failed') return 'bg-n-ruby-3 text-n-ruby-11';
  if (status === 'skipped') return 'bg-n-slate-3 text-n-slate-11';
  return 'bg-n-amber-3 text-n-amber-11';
};

const fetchDeliveries = async () => {
  if (!campaign.value) return;

  isLoading.value = true;
  try {
    const response = await store.dispatch('campaigns/getDeliveries', {
      id: campaign.value.id,
      page: currentPage.value,
      status: statusFilter.value || undefined,
      q: searchQuery.value.trim() || undefined,
    });
    deliveries.value = response.payload;
    currentPage.value = response.meta.current_page;
    totalPages.value = response.meta.total_pages;
    totalCount.value = response.meta.total_count;
  } finally {
    isLoading.value = false;
  }
};

const open = async selectedCampaign => {
  campaign.value = null;
  deliveries.value = [];
  statusFilter.value = '';
  searchQuery.value = '';
  currentPage.value = 1;
  totalPages.value = 1;
  totalCount.value = 0;
  campaign.value = selectedCampaign;
  dialogRef.value.open();
  await fetchDeliveries();
};

const changePage = page => {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return;
  currentPage.value = page;
  fetchDeliveries();
};

watch(statusFilter, () => {
  if (!campaign.value) return;
  currentPage.value = 1;
  fetchDeliveries();
});

const fetchSearchResults = useDebounceFn(() => {
  if (!campaign.value) return;
  currentPage.value = 1;
  fetchDeliveries();
}, 300);

watch(searchQuery, fetchSearchResults);

const exportDeliveries = async () => {
  if (!campaign.value) return;

  isExporting.value = true;
  try {
    const csv = await store.dispatch(
      'campaigns/exportDeliveries',
      campaign.value.id
    );
    downloadCsvFile(`whatsapp-campaign-${campaign.value.id}.csv`, csv);
  } catch {
    useAlert(t('CAMPAIGN.WHATSAPP.DELIVERIES.EXPORT_ERROR'));
  } finally {
    isExporting.value = false;
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
    <div v-else class="flex flex-col gap-3">
      <div class="grid grid-cols-2 gap-3">
        <Input
          v-model="searchQuery"
          :placeholder="t('CAMPAIGN.WHATSAPP.DELIVERIES.SEARCH')"
        />
        <Select v-model="statusFilter" :options="statusOptions" />
      </div>
      <div class="max-h-[55vh] overflow-y-auto rounded-lg border border-n-weak">
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
            <p v-if="delivery.replied_at" class="text-xs text-n-teal-10">
              {{ t('CAMPAIGN.WHATSAPP.DELIVERIES.REPLIED') }}
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
      <div
        v-if="totalPages > 1"
        class="flex items-center justify-between text-sm text-n-slate-11"
      >
        <span>
          {{
            t('CAMPAIGN.WHATSAPP.DELIVERIES.PAGE', {
              current: currentPage,
              total: totalPages,
              count: totalCount,
            })
          }}
        </span>
        <div class="flex gap-2">
          <Button
            color="slate"
            variant="faded"
            size="sm"
            :disabled="currentPage === 1 || isLoading"
            :label="t('CAMPAIGN.WHATSAPP.DELIVERIES.PREVIOUS')"
            @click="changePage(currentPage - 1)"
          />
          <Button
            color="slate"
            variant="faded"
            size="sm"
            :disabled="currentPage === totalPages || isLoading"
            :label="t('CAMPAIGN.WHATSAPP.DELIVERIES.NEXT')"
            @click="changePage(currentPage + 1)"
          />
        </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <Button
          color="slate"
          variant="faded"
          icon="i-lucide-download"
          :is-loading="isExporting"
          :label="t('CAMPAIGN.WHATSAPP.DELIVERIES.EXPORT')"
          @click="exportDeliveries"
        />
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
