<script setup>
// Durian — Reviews dashboard. Google-reviews pulse in-app: rating spread,
// auto vs manual reply, split by store location, and recent low (1-2★) ratings
// that need a person. Reads summary_reports/reviews.
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
      `/api/v2/accounts/${accountId.value}/summary_reports/reviews`,
      { params: { since: from, until: to } }
    );
    report.value = data;
  } catch {
    useAlert(t('REVIEWS_REPORTS.FETCH_ERROR'));
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
      key: 'count',
      label: t('REVIEWS_REPORTS.TILE.COUNT'),
      info: t('REVIEWS_REPORTS.TILE.COUNT_INFO'),
      value: num(s.count),
    },
    {
      key: 'avg',
      label: t('REVIEWS_REPORTS.TILE.AVG'),
      info: t('REVIEWS_REPORTS.TILE.AVG_INFO'),
      value: `${s.avg_stars || 0} ★`,
    },
    {
      key: 'auto',
      label: t('REVIEWS_REPORTS.TILE.AUTO_RATE'),
      info: t('REVIEWS_REPORTS.TILE.AUTO_RATE_INFO'),
      value: `${s.auto_reply_rate || 0}%`,
    },
    {
      key: 'low',
      label: t('REVIEWS_REPORTS.TILE.LOW'),
      info: t('REVIEWS_REPORTS.TILE.LOW_INFO'),
      value: num(s.low_count),
    },
  ];
});

const distributionRows = computed(() => {
  const dist = report.value?.distribution || {};
  const total = Object.values(dist).reduce((a, b) => a + b, 0);
  return [5, 4, 3, 2, 1].map(star => {
    const count = dist[star] || 0;
    return {
      star,
      label: `${star} ★`,
      count,
      pct: total ? Math.round((count / total) * 100) : 0,
    };
  });
});

const locationRows = computed(() => report.value?.by_location || []);
const lowRatings = computed(() => report.value?.low_ratings || []);
const starLabel = stars => '★'.repeat(stars);
const locationSummary = row => `${num(row.count)} · ${row.avg_stars} ★`;
</script>

<template>
  <ReportHeader :header-title="$t('REVIEWS_REPORTS.HEADER')" />
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

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <!-- Rating distribution -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('REVIEWS_REPORTS.SECTION.DISTRIBUTION') }}
        </h3>
        <ul class="flex flex-col gap-2">
          <li
            v-for="row in distributionRows"
            :key="row.star"
            class="flex items-center gap-3 text-sm"
          >
            <span class="w-8 text-n-slate-11">{{ row.label }}</span>
            <span class="flex-1 h-2 rounded-full bg-n-alpha-1 overflow-hidden">
              <span
                class="block h-full rounded-full bg-n-teal-9"
                :style="{ width: `${row.pct}%` }"
              />
            </span>
            <span class="w-10 text-right font-medium text-n-slate-12">
              {{ num(row.count) }}
            </span>
          </li>
        </ul>
      </div>

      <!-- By location -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('REVIEWS_REPORTS.SECTION.LOCATION') }}
        </h3>
        <ul v-if="locationRows.length" class="flex flex-col gap-2">
          <li
            v-for="row in locationRows"
            :key="row.location"
            class="flex items-center justify-between gap-3 text-sm"
          >
            <span class="truncate text-n-slate-11">{{ row.location }}</span>
            <span class="shrink-0 font-medium text-n-slate-12">
              {{ locationSummary(row) }}
            </span>
          </li>
        </ul>
        <p v-else class="text-sm text-n-slate-10">
          {{ $t('REVIEWS_REPORTS.EMPTY') }}
        </p>
      </div>
    </div>

    <!-- Recent low ratings -->
    <div
      class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
    >
      <h3 class="mb-4 text-sm font-medium text-n-slate-12">
        {{ $t('REVIEWS_REPORTS.SECTION.LOW') }}
      </h3>
      <ul v-if="lowRatings.length" class="flex flex-col divide-y divide-n-weak">
        <li
          v-for="(row, idx) in lowRatings"
          :key="idx"
          class="flex items-center justify-between gap-3 py-2 text-sm"
        >
          <span class="flex items-center gap-2 min-w-0">
            <span class="shrink-0 text-n-ruby-9">{{
              starLabel(row.stars)
            }}</span>
            <span class="truncate text-n-slate-12">{{ row.name || '—' }}</span>
            <span class="truncate text-n-slate-10">{{ row.location }}</span>
          </span>
          <span class="shrink-0 text-n-slate-10">{{ row.date }}</span>
        </li>
      </ul>
      <p v-else class="text-sm text-n-slate-10">
        {{ $t('REVIEWS_REPORTS.NO_LOW') }}
      </p>
    </div>
  </div>
</template>
