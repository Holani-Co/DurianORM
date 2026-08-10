<script setup>
// Durian — AI Performance report. How much of the social inbox the assistant
// carried on its own: auto-send rate, confidence, template usage, and which
// intent gates it resolved automatically. Reads summary_reports/ai_performance.
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import ReportHeader from './components/ReportHeader.vue';
import ReportFilters from './components/ReportFilters.vue';
import ReportMetricCard from './components/ReportMetricCard.vue';

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const isLoading = ref(false);
const report = ref(null);

const fetchReport = async ({ from, to }) => {
  if (!from || !to) return;
  isLoading.value = true;
  try {
    const { data } = await axios.get(
      `/api/v2/accounts/${accountId.value}/summary_reports/ai_performance`,
      { params: { since: from, until: to } }
    );
    report.value = data;
  } catch {
    useAlert(t('AI_PERFORMANCE_REPORTS.FETCH_ERROR'));
  } finally {
    isLoading.value = false;
  }
};

const onFilterChange = ({ from, to }) => fetchReport({ from, to });

const num = value => (value || 0).toLocaleString();

const tiles = computed(() => {
  const s = report.value?.summary || {};
  return [
    {
      key: 'auto_replies',
      label: t('AI_PERFORMANCE_REPORTS.TILE.AUTO_REPLIES'),
      info: t('AI_PERFORMANCE_REPORTS.TILE.AUTO_REPLIES_INFO'),
      value: num(s.auto_replies_sent),
    },
    {
      key: 'auto_send_rate',
      label: t('AI_PERFORMANCE_REPORTS.TILE.AUTO_SEND_RATE'),
      info: t('AI_PERFORMANCE_REPORTS.TILE.AUTO_SEND_RATE_INFO'),
      value: `${s.auto_send_rate || 0}%`,
    },
    {
      key: 'avg_confidence',
      label: t('AI_PERFORMANCE_REPORTS.TILE.AVG_CONFIDENCE'),
      info: t('AI_PERFORMANCE_REPORTS.TILE.AVG_CONFIDENCE_INFO'),
      value: `${s.avg_confidence || 0}%`,
    },
    {
      key: 'handoffs',
      label: t('AI_PERFORMANCE_REPORTS.TILE.HANDOFFS'),
      info: t('AI_PERFORMANCE_REPORTS.TILE.HANDOFFS_INFO'),
      value: num(s.handoffs),
    },
  ];
});

const templateRows = computed(() =>
  Object.entries(report.value?.template_usage || {}).map(([code, count]) => ({
    code,
    count,
  }))
);

const confidenceRows = computed(() => {
  const buckets = report.value?.confidence_buckets || {};
  const total = Object.values(buckets).reduce((a, b) => a + b, 0);
  return Object.entries(buckets).map(([band, count]) => ({
    band,
    count,
    pct: total ? Math.round((count / total) * 100) : 0,
  }));
});

const gateRows = computed(() =>
  Object.entries(report.value?.gates || {})
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
);
</script>

<template>
  <ReportHeader :header-title="$t('AI_PERFORMANCE_REPORTS.HEADER')" />
  <div class="flex flex-col gap-4">
    <ReportFilters
      :show-entity-filter="false"
      :show-group-by="false"
      :show-business-hours="false"
      @filter-change="onFilterChange"
    />

    <div
      class="grid grid-cols-2 gap-4 md:grid-cols-4"
      :class="{ 'opacity-50': isLoading }"
    >
      <ReportMetricCard
        v-for="tile in tiles"
        :key="tile.key"
        :label="tile.label"
        :info-text="tile.info"
        :value="tile.value"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <!-- Template usage -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('AI_PERFORMANCE_REPORTS.SECTION.TEMPLATES') }}
        </h3>
        <ul v-if="templateRows.length" class="flex flex-col gap-2">
          <li
            v-for="row in templateRows"
            :key="row.code"
            class="flex items-center justify-between gap-3 text-sm"
          >
            <span class="truncate text-n-slate-11">{{ row.code }}</span>
            <span class="font-medium text-n-slate-12">{{
              num(row.count)
            }}</span>
          </li>
        </ul>
        <p v-else class="text-sm text-n-slate-10">
          {{ $t('AI_PERFORMANCE_REPORTS.EMPTY') }}
        </p>
      </div>

      <!-- Confidence distribution -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('AI_PERFORMANCE_REPORTS.SECTION.CONFIDENCE') }}
        </h3>
        <ul class="flex flex-col gap-2">
          <li
            v-for="row in confidenceRows"
            :key="row.band"
            class="flex items-center gap-3 text-sm"
          >
            <span class="w-16 text-n-slate-11">{{ row.band }}</span>
            <span class="flex-1 h-2 rounded-full bg-n-alpha-1 overflow-hidden">
              <span
                class="block h-full rounded-full bg-n-teal-9"
                :style="{ width: `${row.pct}%` }"
              />
            </span>
            <span class="w-8 text-right font-medium text-n-slate-12">
              {{ num(row.count) }}
            </span>
          </li>
        </ul>
      </div>

      <!-- Gates answered automatically -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('AI_PERFORMANCE_REPORTS.SECTION.GATES') }}
        </h3>
        <ul class="flex flex-col gap-2">
          <li
            v-for="row in gateRows"
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
    </div>
  </div>
</template>
