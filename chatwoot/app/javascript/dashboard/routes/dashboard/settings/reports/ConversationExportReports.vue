<script setup>
// Durian — Conversation export (ISS-672). Download every conversation of a period
// — in any inboxes, any channel — as one Excel file: a Conversations sheet (one
// row each, with its ID and link) and a Messages sheet with the full history, so
// nobody scrolls months back to read a chat.
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useStore } from 'vuex';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import ReportHeader from './components/ReportHeader.vue';
import ReportFilters from './components/ReportFilters.vue';

const { t } = useI18n();
const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');
const inboxes = useMapGetter('inboxes/getInboxes');
const axios = window.axios;

const range = ref({ from: 0, to: 0 });
const selected = ref([]);
const includeMessages = ref(true);
const downloading = ref(false);

onMounted(() => store.dispatch('inboxes/get'));

const sortedInboxes = computed(() =>
  [...(inboxes.value || [])].sort((a, b) => a.name.localeCompare(b.name))
);
const tooLong = computed(
  () => range.value.to - range.value.from > 93 * 24 * 60 * 60
);

const onFilterChange = ({ from, to }) => {
  range.value = { from, to };
};

const toggle = id => {
  selected.value = selected.value.includes(id)
    ? selected.value.filter(x => x !== id)
    : [...selected.value, id];
};

const download = async () => {
  if (downloading.value || !range.value.from || tooLong.value) return;
  downloading.value = true;
  try {
    const { data } = await axios.get(
      `/api/v1/accounts/${accountId.value}/orm_exports/conversations`,
      {
        params: {
          since: range.value.from,
          until: range.value.to,
          inbox_ids: selected.value.join(','),
          messages: includeMessages.value ? 'true' : 'false',
        },
        responseType: 'blob',
      }
    );
    const url = URL.createObjectURL(data);
    const link = document.createElement('a');
    link.href = url;
    link.download = `durian-conversations-${new Date().toISOString().slice(0, 10)}.xlsx`;
    link.click();
    URL.revokeObjectURL(url);
  } catch {
    useAlert(t('CONVERSATION_EXPORT_REPORTS.ERROR'));
  } finally {
    downloading.value = false;
  }
};
</script>

<template>
  <ReportHeader :header-title="$t('CONVERSATION_EXPORT_REPORTS.HEADER')" />
  <div class="flex flex-col gap-4">
    <p class="text-sm text-n-slate-11">
      {{ $t('CONVERSATION_EXPORT_REPORTS.DESCRIPTION') }}
    </p>
    <ReportFilters
      :show-entity-filter="false"
      :show-group-by="false"
      :show-business-hours="false"
      @filter-change="onFilterChange"
    />

    <div class="p-4 border rounded-xl border-n-weak">
      <div class="mb-1 text-sm font-medium text-n-slate-12">
        {{ $t('CONVERSATION_EXPORT_REPORTS.INBOXES') }}
      </div>
      <p class="mb-3 text-xs text-n-slate-11">
        {{ $t('CONVERSATION_EXPORT_REPORTS.INBOXES_HINT') }}
      </p>
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <label
          v-for="inbox in sortedInboxes"
          :key="inbox.id"
          class="flex items-center gap-2 text-sm text-n-slate-12"
        >
          <input
            type="checkbox"
            :checked="selected.includes(inbox.id)"
            @change="toggle(inbox.id)"
          />
          {{ inbox.name }}
        </label>
      </div>
    </div>

    <label class="flex items-center gap-2 text-sm text-n-slate-12">
      <input v-model="includeMessages" type="checkbox" />
      {{ $t('CONVERSATION_EXPORT_REPORTS.INCLUDE_MESSAGES') }}
    </label>

    <div class="flex flex-wrap items-center gap-3">
      <button
        type="button"
        class="flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="downloading || !range.from || tooLong"
        @click="download"
      >
        <span
          class="size-4"
          :class="
            downloading ? 'i-lucide-loader-2 animate-spin' : 'i-lucide-download'
          "
        />
        {{
          downloading
            ? $t('CONVERSATION_EXPORT_REPORTS.DOWNLOADING')
            : $t('CONVERSATION_EXPORT_REPORTS.DOWNLOAD')
        }}
      </button>
      <span v-if="tooLong" class="text-sm text-n-ruby-11">
        {{ $t('CONVERSATION_EXPORT_REPORTS.TOO_LONG') }}
      </span>
    </div>
  </div>
</template>
