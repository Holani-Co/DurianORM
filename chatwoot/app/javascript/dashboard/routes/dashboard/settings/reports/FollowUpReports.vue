<script setup>
// Durian — Follow-ups report. The client marks conversations that need
// following up with their own labels; an admin picks which labels count here
// (saved on the account, so it's managed entirely from Chatwoot). Shows the
// open backlog + period total per label, with drill-through.
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAccount } from 'dashboard/composables/useAccount';
import { useAlert } from 'dashboard/composables';
import ReportHeader from './components/ReportHeader.vue';
import ReportFilters from './components/ReportFilters.vue';

const { t } = useI18n();
const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');
const { accountScopedRoute } = useAccount();
const allLabels = useMapGetter('labels/getLabels');
const axios = window.axios;

const isLoading = ref(false);
const rows = ref([]);
const selected = ref([]);
const range = ref({ from: 0, to: 0 });
const configuring = ref(false);
const draft = ref([]);
const saving = ref(false);

onMounted(() => store.dispatch('labels/get'));

const fetchReport = async ({ from, to }) => {
  if (!from || !to) return;
  isLoading.value = true;
  try {
    const { data } = await axios.get(
      `/api/v1/accounts/${accountId.value}/follow_up_report`,
      { params: { since: from, until: to } }
    );
    rows.value = data.rows || [];
    selected.value = data.selected || [];
  } catch {
    useAlert(t('FOLLOW_UP_REPORTS.FETCH_ERROR'));
  } finally {
    isLoading.value = false;
  }
};

const onFilterChange = ({ from, to }) => {
  range.value = { from, to };
  fetchReport({ from, to });
};

const openConfig = () => {
  draft.value = [...selected.value];
  configuring.value = true;
};

const toggle = title => {
  const i = draft.value.indexOf(title);
  if (i === -1) draft.value.push(title);
  else draft.value.splice(i, 1);
};

const saveConfig = async () => {
  saving.value = true;
  try {
    const { data } = await axios.patch(
      `/api/v1/accounts/${accountId.value}/follow_up_report`,
      { labels: draft.value }
    );
    selected.value = data.selected || [];
    configuring.value = false;
    fetchReport(range.value);
  } catch {
    useAlert(t('FOLLOW_UP_REPORTS.SAVE_ERROR'));
  } finally {
    saving.value = false;
  }
};

const labelRoute = title =>
  accountScopedRoute('label_conversations', { label: title });
const num = v => (v || 0).toLocaleString();
</script>

<template>
  <ReportHeader :header-title="$t('FOLLOW_UP_REPORTS.HEADER')" />
  <div class="flex flex-col gap-4">
    <ReportFilters
      :show-entity-filter="false"
      :show-group-by="false"
      :show-business-hours="false"
      @filter-change="onFilterChange"
    />

    <div class="flex items-center justify-between">
      <p class="text-sm text-n-slate-11">
        {{ $t('FOLLOW_UP_REPORTS.SUBTITLE') }}
      </p>
      <button
        type="button"
        class="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg outline-1 outline outline-n-container bg-n-solid-2 text-n-slate-12 hover:bg-n-alpha-2"
        @click="openConfig"
      >
        <span class="size-3.5 i-lucide-settings-2" />
        {{ $t('FOLLOW_UP_REPORTS.CONFIGURE') }}
      </button>
    </div>

    <!-- Label picker -->
    <div
      v-if="configuring"
      class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
    >
      <h3 class="mb-1 text-sm font-medium text-n-slate-12">
        {{ $t('FOLLOW_UP_REPORTS.PICKER_TITLE') }}
      </h3>
      <p class="mb-4 text-xs text-n-slate-10">
        {{ $t('FOLLOW_UP_REPORTS.PICKER_HINT') }}
      </p>
      <div
        v-if="allLabels.length"
        class="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3"
      >
        <label
          v-for="l in allLabels"
          :key="l.id"
          class="flex items-center gap-2 text-sm cursor-pointer text-n-slate-12"
        >
          <input
            type="checkbox"
            :checked="draft.includes(l.title)"
            class="accent-n-brand"
            @change="toggle(l.title)"
          />
          <span class="truncate">{{ l.title }}</span>
        </label>
      </div>
      <p v-else class="text-sm text-n-slate-10">
        {{ $t('FOLLOW_UP_REPORTS.NO_LABELS') }}
      </p>
      <div class="flex justify-end gap-2 mt-5">
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:bg-n-alpha-2"
          @click="configuring = false"
        >
          {{ $t('FOLLOW_UP_REPORTS.CANCEL') }}
        </button>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg bg-n-brand text-white hover:opacity-90 disabled:opacity-50"
          :disabled="saving"
          @click="saveConfig"
        >
          {{ $t('FOLLOW_UP_REPORTS.SAVE') }}
        </button>
      </div>
    </div>

    <!-- Report -->
    <div
      class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      :class="{ 'opacity-50': isLoading }"
    >
      <div
        v-if="rows.length"
        class="grid grid-cols-[1fr_auto_auto] gap-x-8 gap-y-1 items-center"
      >
        <span class="text-xs font-medium uppercase text-n-slate-10">
          {{ $t('FOLLOW_UP_REPORTS.COL_LABEL') }}
        </span>
        <span class="text-xs font-medium uppercase text-right text-n-slate-10">
          {{ $t('FOLLOW_UP_REPORTS.COL_OPEN') }}
        </span>
        <span class="text-xs font-medium uppercase text-right text-n-slate-10">
          {{ $t('FOLLOW_UP_REPORTS.COL_TOTAL') }}
        </span>
        <template v-for="row in rows" :key="row.label">
          <router-link
            :to="labelRoute(row.label)"
            class="py-2 text-sm truncate text-n-slate-11 hover:text-n-slate-12"
          >
            {{ row.label }}
          </router-link>
          <span class="py-2 text-sm font-medium text-right text-n-slate-12">
            {{ num(row.open) }}
          </span>
          <span class="py-2 text-sm text-right text-n-slate-11">
            {{ num(row.total) }}
          </span>
        </template>
      </div>
      <p v-else class="text-sm text-n-slate-10">
        {{ $t('FOLLOW_UP_REPORTS.EMPTY') }}
      </p>
    </div>
  </div>
</template>
