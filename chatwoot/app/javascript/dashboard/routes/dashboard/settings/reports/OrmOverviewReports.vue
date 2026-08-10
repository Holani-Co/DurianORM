<script setup>
// Durian — ORM Overview health board. One screen: how much of the inbound the
// AI handled on its own, how much still needs a person, and what business it
// generated (deals, tickets) plus the review pulse — for a date range. Reads a
// single bridge-fed endpoint (summary_reports/orm_overview).
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

const fetchOverview = async ({ from, to }) => {
  if (!from || !to) return;
  isLoading.value = true;
  try {
    const { data } = await axios.get(
      `/api/v2/accounts/${accountId.value}/summary_reports/orm_overview`,
      { params: { since: from, until: to } }
    );
    report.value = data;
  } catch {
    useAlert(t('ORM_OVERVIEW_REPORTS.FETCH_ERROR'));
  } finally {
    isLoading.value = false;
  }
};

const onFilterChange = ({ from, to }) => fetchOverview({ from, to });

const num = value => (value || 0).toLocaleString();

const durationLabel = computed(() => {
  const seconds = report.value?.first_response?.avg_seconds || 0;
  if (!seconds) return '—';
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
});

const autoHandledPct = computed(() => {
  const total = report.value?.conversations?.total || 0;
  const handled = report.value?.ai?.auto_handled_conversations || 0;
  return total ? `${Math.round((handled / total) * 100)}%` : '0%';
});

const byChannel = computed(() =>
  Object.entries(report.value?.conversations?.by_channel || {}).sort(
    (a, b) => b[1] - a[1]
  )
);

const categories = computed(() =>
  Object.entries(report.value?.categories || {}).sort((a, b) => b[1] - a[1])
);

const humanizeCategory = key =>
  key.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());

const reviewRows = computed(() => {
  const dist = report.value?.reviews?.distribution || {};
  const total = report.value?.reviews?.count || 0;
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
</script>

<template>
  <ReportHeader :header-title="$t('ORM_OVERVIEW_REPORTS.HEADER')" />
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
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.CONVERSATIONS')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.CONVERSATIONS_INFO')"
        :value="num(report?.conversations?.total)"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.AUTO_HANDLED')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.AUTO_HANDLED_INFO')"
        :value="autoHandledPct"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.AUTO_REPLIES')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.AUTO_REPLIES_INFO')"
        :value="num(report?.ai?.auto_replies_sent)"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.AGENT_NEEDED')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.AGENT_NEEDED_INFO')"
        :value="num(report?.ai?.agent_needed_open)"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.FIRST_RESPONSE')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.FIRST_RESPONSE_INFO')"
        :value="durationLabel"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.DEALS')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.DEALS_INFO')"
        :value="num(report?.deals?.created)"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.TICKETS')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.TICKETS_INFO')"
        :value="num(report?.tickets?.raised)"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
      <ReportMetricCard
        :label="$t('ORM_OVERVIEW_REPORTS.TILE.AVG_RATING')"
        :info-text="$t('ORM_OVERVIEW_REPORTS.TILE.AVG_RATING_INFO')"
        :value="`${report?.reviews?.avg_stars || 0} ★`"
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      />
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <!-- Conversations by channel -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('ORM_OVERVIEW_REPORTS.SECTION.BY_CHANNEL') }}
        </h3>
        <ul v-if="byChannel.length" class="flex flex-col gap-2">
          <li
            v-for="[name, count] in byChannel"
            :key="name"
            class="flex items-center justify-between text-sm"
          >
            <span class="text-n-slate-11">{{ name }}</span>
            <span class="font-medium text-n-slate-12">{{ num(count) }}</span>
          </li>
        </ul>
        <p v-else class="text-sm text-n-slate-10">
          {{ $t('ORM_OVERVIEW_REPORTS.EMPTY') }}
        </p>
      </div>

      <!-- Enquiry mix -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('ORM_OVERVIEW_REPORTS.SECTION.CATEGORIES') }}
        </h3>
        <ul v-if="categories.length" class="flex flex-col gap-2">
          <li
            v-for="[key, count] in categories"
            :key="key"
            class="flex items-center justify-between text-sm"
          >
            <span class="text-n-slate-11">{{ humanizeCategory(key) }}</span>
            <span class="font-medium text-n-slate-12">{{ num(count) }}</span>
          </li>
        </ul>
        <p v-else class="text-sm text-n-slate-10">
          {{ $t('ORM_OVERVIEW_REPORTS.EMPTY') }}
        </p>
      </div>

      <!-- Reviews breakdown -->
      <div
        class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      >
        <h3 class="mb-4 text-sm font-medium text-n-slate-12">
          {{ $t('ORM_OVERVIEW_REPORTS.SECTION.REVIEWS') }}
        </h3>
        <ul class="flex flex-col gap-2">
          <li
            v-for="row in reviewRows"
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
            <span class="w-8 text-right font-medium text-n-slate-12">
              {{ num(row.count) }}
            </span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
