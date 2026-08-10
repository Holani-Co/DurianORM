<script setup>
// Durian — CRM / Lead funnel report. The sales journey the ORM generates:
// enquiries routed → qualified → deals created, plus deal mix by vertical and
// enquiry category. Reads summary_reports/crm_funnel.
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAccount } from 'dashboard/composables/useAccount';
import { useAlert } from 'dashboard/composables';
import ReportHeader from './components/ReportHeader.vue';
import ReportFilters from './components/ReportFilters.vue';
import ReportMetricCard from './components/ReportMetricCard.vue';

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const { accountScopedRoute } = useAccount();
const axios = window.axios;

const isLoading = ref(false);
const report = ref(null);

const fetchReport = async ({ from, to }) => {
  if (!from || !to) return;
  isLoading.value = true;
  try {
    const { data } = await axios.get(
      `/api/v2/accounts/${accountId.value}/summary_reports/crm_funnel`,
      { params: { since: from, until: to } }
    );
    report.value = data;
  } catch {
    useAlert(t('CRM_FUNNEL_REPORTS.FETCH_ERROR'));
  } finally {
    isLoading.value = false;
  }
};

const onFilterChange = ({ from, to }) => fetchReport({ from, to });

const num = value => (value || 0).toLocaleString();

const labelRoute = label =>
  accountScopedRoute('label_conversations', { label });

// Funnel stages as proportional bars, widest stage = 100%.
const funnelRows = computed(() => {
  const stages = report.value?.funnel || [];
  const max = Math.max(1, ...stages.map(s => s.count));
  return stages.map(s => ({
    ...s,
    pct: Math.round((s.count / max) * 100),
    to: labelRoute(s.label),
  }));
});

const verticalRows = computed(() =>
  Object.entries(report.value?.by_vertical || {})
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
);

const humanizeCategory = key =>
  key.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());

const categoryRows = computed(() =>
  Object.entries(report.value?.by_category || {})
    .map(([key, count]) => ({ name: humanizeCategory(key), count }))
    .sort((a, b) => b.count - a.count)
);
</script>

<template>
  <ReportHeader :header-title="$t('CRM_FUNNEL_REPORTS.HEADER')" />
  <div class="flex flex-col gap-4">
    <ReportFilters
      :show-entity-filter="false"
      :show-group-by="false"
      :show-business-hours="false"
      @filter-change="onFilterChange"
    />

    <div
      class="grid grid-cols-1 gap-4 lg:grid-cols-3"
      :class="{ 'opacity-50': isLoading }"
    >
      <!-- Funnel -->
      <div
        class="lg:col-span-2 shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('CRM_FUNNEL_REPORTS.SECTION.FUNNEL') }}
        </h3>
        <ul class="flex flex-col gap-3">
          <li v-for="row in funnelRows" :key="row.stage">
            <router-link :to="row.to" class="block group">
              <div class="flex items-center justify-between mb-1 text-sm">
                <span class="text-n-slate-11 group-hover:text-n-slate-12">
                  {{ row.stage }}
                </span>
                <span class="font-medium text-n-slate-12">
                  {{ num(row.count) }}
                </span>
              </div>
              <span class="block h-3 rounded-full bg-n-alpha-1 overflow-hidden">
                <span
                  class="block h-full rounded-full bg-n-teal-9 group-hover:bg-n-teal-10"
                  :style="{ width: `${row.pct}%` }"
                />
              </span>
            </router-link>
          </li>
        </ul>
      </div>

      <!-- Conversion -->
      <ReportMetricCard
        :label="$t('CRM_FUNNEL_REPORTS.TILE.CONVERSION')"
        :info-text="$t('CRM_FUNNEL_REPORTS.TILE.CONVERSION_INFO')"
        :value="`${report?.conversion_rate || 0}%`"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <!-- Deals by vertical -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('CRM_FUNNEL_REPORTS.SECTION.VERTICAL') }}
        </h3>
        <ul class="flex flex-col gap-2">
          <li
            v-for="row in verticalRows"
            :key="row.name"
            class="flex items-center justify-between text-sm"
          >
            <span class="text-n-slate-11">{{ row.name }}</span>
            <span class="font-medium text-n-slate-12">{{
              num(row.count)
            }}</span>
          </li>
        </ul>
      </div>

      <!-- Deals by category -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('CRM_FUNNEL_REPORTS.SECTION.CATEGORY') }}
        </h3>
        <ul v-if="categoryRows.length" class="flex flex-col gap-2">
          <li
            v-for="row in categoryRows"
            :key="row.name"
            class="flex items-center justify-between text-sm"
          >
            <span class="text-n-slate-11">{{ row.name }}</span>
            <span class="font-medium text-n-slate-12">{{
              num(row.count)
            }}</span>
          </li>
        </ul>
        <p v-else class="text-sm text-n-slate-10">
          {{ $t('CRM_FUNNEL_REPORTS.EMPTY') }}
        </p>
      </div>
    </div>
  </div>
</template>
