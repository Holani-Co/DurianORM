<script setup>
// Durian — Forwarded Emails report. For one email category (Collaboration,
// Careers, …), every customer email the ORM forwarded to a team in a Mon–Sun
// week, who received it and what came of it. Send emails the report (summary +
// Excel) to the address entered, pre-filled with the category's forward address.
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { addDays, format, startOfWeek, subWeeks } from 'date-fns';
import { useMapGetter } from 'dashboard/composables/store';
import { useAccount } from 'dashboard/composables/useAccount';
import { useAlert } from 'dashboard/composables';
import ReportHeader from './components/ReportHeader.vue';
import ReportMetricCard from './components/ReportMetricCard.vue';

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const { accountScopedRoute } = useAccount();
const axios = window.axios;
const baseUrl = () =>
  `/api/v1/accounts/${accountId.value}/forwarded_email_report`;

// The last 13 Mon–Sun weeks, newest first; defaults to the last full week.
const thisMonday = startOfWeek(new Date(), { weekStartsOn: 1 });
const weeks = Array.from({ length: 13 }, (_, i) => {
  const start = subWeeks(thisMonday, i);
  const label = `${format(start, 'd MMM')} – ${format(addDays(start, 6), 'd MMM yyyy')}`;
  return {
    value: format(start, 'yyyy-MM-dd'),
    label: i ? label : t('FORWARDED_REPORTS.THIS_WEEK', { week: label }),
  };
});

const categories = ref([]);
const category = ref('');
const week = ref(weeks[1].value);
const recipients = ref('');
const report = ref(null);
const isLoading = ref(false);
const isSending = ref(false);

const fetchReport = async () => {
  if (!category.value) return;
  isLoading.value = true;
  try {
    const { data } = await axios.get(baseUrl(), {
      params: { category: category.value, week: week.value },
    });
    report.value = data;
  } catch {
    useAlert(t('FORWARDED_REPORTS.FETCH_ERROR'));
  } finally {
    isLoading.value = false;
  }
};

const selectedCategory = computed(() =>
  categories.value.find(c => c.key === category.value)
);

const onCategoryChange = () => {
  recipients.value = selectedCategory.value?.forward_to || '';
  fetchReport();
};

onMounted(async () => {
  try {
    const { data } = await axios.get(`${baseUrl()}/categories`);
    categories.value = data;
    category.value = data[0]?.key || '';
    onCategoryChange();
  } catch {
    useAlert(t('FORWARDED_REPORTS.CATEGORIES_ERROR'));
  }
});

const send = async () => {
  isSending.value = true;
  try {
    const { data } = await axios.post(`${baseUrl()}/deliver`, {
      category: category.value,
      category_name: selectedCategory.value?.name,
      week: week.value,
      recipients: recipients.value,
    });
    useAlert(
      t('FORWARDED_REPORTS.SEND.SUCCESS', {
        recipients: data.recipients.join(', '),
      })
    );
  } catch (error) {
    useAlert(
      error.response?.status === 422
        ? t('FORWARDED_REPORTS.SEND.INVALID')
        : t('FORWARDED_REPORTS.SEND.ERROR')
    );
  } finally {
    isSending.value = false;
  }
};

const num = value => (value || 0).toLocaleString();

const tiles = computed(() => {
  const s = report.value?.totals || {};
  return [
    {
      key: 'forwarded',
      label: t('FORWARDED_REPORTS.TILE.FORWARDED'),
      info: t('FORWARDED_REPORTS.TILE.FORWARDED_INFO'),
      value: num(s.forwarded),
    },
    {
      key: 'deals',
      label: t('FORWARDED_REPORTS.TILE.DEALS'),
      info: t('FORWARDED_REPORTS.TILE.DEALS_INFO'),
      value: num(s.deals),
    },
    {
      key: 'tickets',
      label: t('FORWARDED_REPORTS.TILE.TICKETS'),
      info: t('FORWARDED_REPORTS.TILE.TICKETS_INFO'),
      value: num(s.tickets),
    },
    {
      key: 'resolved',
      label: t('FORWARDED_REPORTS.TILE.RESOLVED'),
      info: t('FORWARDED_REPORTS.TILE.RESOLVED_INFO'),
      value: num(s.resolved),
    },
  ];
});

const byRecipient = computed(() =>
  Object.entries(report.value?.totals?.by_recipient || {})
);
const rows = computed(() => report.value?.rows || []);
const dealLabel = row =>
  row.deal === 'Yes' ? row.deal_no || t('FORWARDED_REPORTS.DEAL_YES') : '—';
const conversationRoute = id =>
  accountScopedRoute('inbox_conversation', { conversation_id: id });
</script>

<template>
  <ReportHeader :header-title="$t('FORWARDED_REPORTS.HEADER')" />
  <div class="flex flex-col gap-4">
    <p class="text-sm text-n-slate-11">
      {{ $t('FORWARDED_REPORTS.SUBTITLE') }}
    </p>

    <div class="flex flex-wrap items-center gap-2">
      <select
        v-model="category"
        :aria-label="$t('FORWARDED_REPORTS.CATEGORY')"
        class="!mb-0 !w-auto px-3 py-1.5 text-sm rounded-lg outline-1 outline outline-n-container bg-n-solid-2 text-n-slate-12 cursor-pointer"
        @change="onCategoryChange"
      >
        <option v-for="c in categories" :key="c.key" :value="c.key">
          {{ c.name }}
        </option>
      </select>
      <select
        v-model="week"
        :aria-label="$t('FORWARDED_REPORTS.WEEK')"
        class="!mb-0 !w-auto px-3 py-1.5 text-sm rounded-lg outline-1 outline outline-n-container bg-n-solid-2 text-n-slate-12 cursor-pointer"
        @change="fetchReport"
      >
        <option v-for="w in weeks" :key="w.value" :value="w.value">
          {{ w.label }}
        </option>
      </select>
    </div>

    <!-- Send the selected category + week to the address(es) entered. -->
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-sm text-n-slate-11">
        {{ $t('FORWARDED_REPORTS.SEND.LABEL') }}
      </span>
      <input
        v-model="recipients"
        type="text"
        :placeholder="$t('FORWARDED_REPORTS.SEND.PLACEHOLDER')"
        class="!mb-0 flex-1 min-w-64 max-w-md px-3 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
      />
      <button
        type="button"
        class="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-n-brand text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="isSending || !category || !recipients.trim()"
        @click="send"
      >
        <span
          class="size-3.5"
          :class="
            isSending ? 'i-lucide-loader-2 animate-spin' : 'i-lucide-send'
          "
        />
        {{ $t('FORWARDED_REPORTS.SEND.BUTTON') }}
      </button>
    </div>

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

    <!-- Who received them -->
    <div
      v-if="byRecipient.length"
      class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
    >
      <h3 class="mb-4 text-sm font-medium text-n-slate-12">
        {{ $t('FORWARDED_REPORTS.SECTION.RECIPIENTS') }}
      </h3>
      <ul class="flex flex-col gap-2 max-w-md">
        <li
          v-for="[email, count] in byRecipient"
          :key="email"
          class="flex items-center justify-between gap-3 text-sm"
        >
          <span class="truncate text-n-slate-11">{{ email }}</span>
          <span class="shrink-0 font-medium text-n-slate-12">
            {{ num(count) }}
          </span>
        </li>
      </ul>
    </div>

    <!-- The forwarded emails -->
    <div
      class="shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2 px-6 py-5"
      :class="{ 'opacity-50': isLoading }"
    >
      <h3 class="mb-4 text-sm font-medium text-n-slate-12">
        {{ $t('FORWARDED_REPORTS.SECTION.EMAILS') }}
      </h3>
      <div v-if="rows.length" class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-left uppercase text-n-slate-10">
              <th class="py-2 pr-4 font-medium">
                {{ $t('FORWARDED_REPORTS.COL.DATE') }}
              </th>
              <th class="py-2 pr-4 font-medium">
                {{ $t('FORWARDED_REPORTS.COL.CUSTOMER') }}
              </th>
              <th class="py-2 pr-4 font-medium">
                {{ $t('FORWARDED_REPORTS.COL.CASE') }}
              </th>
              <th class="py-2 pr-4 font-medium">
                {{ $t('FORWARDED_REPORTS.COL.FORWARDED_TO') }}
              </th>
              <th class="py-2 pr-4 font-medium">
                {{ $t('FORWARDED_REPORTS.COL.DEAL') }}
              </th>
              <th class="py-2 font-medium">
                {{ $t('FORWARDED_REPORTS.COL.STATUS') }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-n-weak">
            <tr
              v-for="row in rows"
              :key="row.conversation_id"
              class="align-top"
            >
              <td class="py-2 pr-4 whitespace-nowrap text-n-slate-11">
                {{ row.forwarded_at }}
              </td>
              <td class="py-2 pr-4 text-n-slate-12">
                <div>{{ row.customer || '—' }}</div>
                <div class="text-xs text-n-slate-10">{{ row.email }}</div>
                <div class="text-xs text-n-slate-10">{{ row.mobile }}</div>
              </td>
              <td class="py-2 pr-4 min-w-64">
                <router-link
                  :to="conversationRoute(row.conversation_id)"
                  class="font-medium text-n-slate-12 hover:underline"
                >
                  {{ row.subject || $t('FORWARDED_REPORTS.NO_SUBJECT') }}
                </router-link>
                <div class="text-xs text-n-slate-10 line-clamp-2">
                  {{ row.message }}
                </div>
              </td>
              <td class="py-2 pr-4 text-n-slate-11">
                <div>{{ row.forwarded_to }}</div>
                <div class="text-xs text-n-slate-10">
                  {{ row.forwarded_by }}
                </div>
              </td>
              <td class="py-2 pr-4 text-n-slate-11">
                <div>{{ dealLabel(row) }}</div>
                <div v-if="row.deal === 'Yes'" class="text-xs text-n-slate-10">
                  {{ row.deal_stage }}
                </div>
              </td>
              <td class="py-2 text-n-slate-11">
                <div>{{ row.status }}</div>
                <div v-if="row.ticket_no" class="text-xs text-n-slate-10">
                  {{
                    $t('FORWARDED_REPORTS.TICKET', {
                      number: row.ticket_no,
                      status: row.ticket_status || '',
                    })
                  }}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="text-sm text-n-slate-10">
        {{ $t('FORWARDED_REPORTS.EMPTY') }}
      </p>
    </div>
  </div>
</template>
